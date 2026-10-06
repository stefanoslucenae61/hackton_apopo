VERSION = "2.2.2"
import re
import math
import json
import os
import time
import uuid
import pandas as pd
from datetime import datetime
import streamlit as st
from openai import OpenAI
import duckdb
from pathlib import Path
import altair as alt
from dotenv import load_dotenv
from io import StringIO
load_dotenv()
st.set_page_config(layout="wide")
if not os.getenv("OPENAI_API_KEY"):
    st.error("Set OPENAI_API_KEY in your .env file before starting the app.")
    st.stop()
client = OpenAI()
def quote_identifier(value):
    return '"' + value.replace('"', '""') + '"'
# Explicit date columns from the Excel export; never convert numeric IDs.
EXCEL_DATE_COLUMNS = {"SESSION_DATE"}


def normalize_excel_dates(connection, table_name):
    """Convert declared Excel date columns using the workbook's 1900 date system."""
    columns = connection.execute(f"DESCRIBE {table_name}").fetchall()
    numeric_types = {"TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT",
                     "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT", "FLOAT", "DOUBLE"}
    for column, dtype, *_ in columns:
        if column.upper() not in EXCEL_DATE_COLUMNS:
            continue
        if dtype not in numeric_types and not dtype.startswith("DECIMAL"):
            continue
        quoted = quote_identifier(column)
        invalid = connection.execute(
            f"SELECT COUNT(*) FROM {table_name} WHERE {quoted} IS NOT NULL "
            f"AND (NOT isfinite(CAST({quoted} AS DOUBLE)) OR {quoted} < 20000 OR {quoted} > 80000)"
        ).fetchone()[0]
        if invalid:
            raise ValueError(
                f"{column} has {invalid} values outside the expected Excel date range. "
                "Check its date encoding before converting it."
            )
        connection.execute(
            f"ALTER TABLE {table_name} ALTER COLUMN {quoted} TYPE TIMESTAMP USING "
            f"(TIMESTAMP '1899-12-30 00:00:00' + CAST({quoted} AS DOUBLE) * INTERVAL '1 day')"
        )


def load_gold_folder(folder):
    """Load each Parquet file as a table in a temporary, in-memory SQL engine."""
    folder = Path(folder).resolve()
    if not folder.is_dir():
        raise ValueError(f"Gold folder does not exist: {folder}")
    files = sorted(folder.rglob("*.parquet"))
    if not files:
        raise ValueError(f"No Parquet files found in {folder}")
    connection = duckdb.connect(database=":memory:")
    connection.execute("CREATE SCHEMA gold")
    tables = []
    names = set()
    try:
        for path in files:
            if not path.resolve().is_relative_to(folder):
                raise ValueError(f"Parquet file points outside gold: {path.name}")
            relative = path.relative_to(folder)
            name = re.sub(r"[^a-zA-Z0-9_]", "_", "__".join(relative.with_suffix("").parts))
            if not name or name[0].isdigit():
                name = "table_" + name
            if name.lower() in names:
                raise ValueError(f"Duplicate table name after normalization: {name}")
            names.add(name.lower())
            relation = connection.read_parquet(str(path))
            relation.create_view("_gold_source", replace=True)
            connection.execute(f"CREATE TABLE gold.{quote_identifier(name)} AS SELECT * FROM _gold_source")
            connection.execute("DROP VIEW _gold_source")
            table_name = f"gold.{quote_identifier(name)}"
            normalize_excel_dates(connection, table_name)
            column_info = connection.execute(f"DESCRIBE {table_name}").fetchall()
            tables.append({"db": "gold", "schema": "gold", "table": name,
                           "columns": [column[0] for column in column_info],
                           "column_types": {column[0]: column[1] for column in column_info},
                           "source_file": relative.as_posix()})
        # Generated queries can only access loaded data, not external files.
        connection.execute("SET enable_external_access = false")
    except Exception:
        connection.close()
        raise
    return connection, tables
GOLD_FOLDER = Path(__file__).resolve().parent.parent / "data" / "bronze"
try:
    gold_connection, gold_tables = load_gold_folder(GOLD_FOLDER)
except Exception as error:
    st.error(f"Could not load gold data: {error}")
    st.stop()
def fetch_dataframe(query):
    statements = gold_connection.extract_statements(query)
    if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
        raise ValueError("Only one SELECT query is allowed.")
    return gold_connection.execute(query).fetchdf()
def qualified_table(schema, name):
    return f"{quote_identifier(schema)}.{quote_identifier(name)}" if schema else quote_identifier(name)
def get_data_model():
    return [dict(table) for table in gold_tables]
def generate_semantic_metadata(table_info):
    """Use LLM to generate semantic metadata for a table"""
    schema = table_info.get('schema', '')
    name = table_info.get('table', '')
    columns = table_info.get('columns', [])
    # Fetch a sample of actual data to give the LLM context
    try:
        table_name = qualified_table(schema, name)
        sample_query = f"SELECT * FROM {table_name} LIMIT 5"
        sample_df = fetch_dataframe(sample_query)
        sample_data = sample_df.to_string(index=False)
    except:
        sample_data = "No sample data available"
        add_log("no sample data", sample_data) # LOG
    prompt = f"""
You are a data analyst. Analyze this database table and generate semantic metadata for it.
Table: {schema}.{name}
Columns: {', '.join(columns)}
Sample data:
{sample_data}
Return a JSON object with exactly this structure:
{{
    "table_description": "one sentence describing what this table represents in business terms",
    "column_descriptions": {{
        "column_name": "business description of what this column means, include data type hints and example values if obvious"
    }},
    "primary_key": "the column that is most likely the primary key, or empty string",
    "relationships": [
        {{
            "from_column": "column in this table that is a foreign key",
            "to_table": "schema.table it likely references",
            "to_column": "column it references (usually id)",
            "join_type": "LEFT"
        }}
    ],
    "default_filters": [
        "any filters that should always be applied e.g. status != 'deleted'"
    ],
    "common_metrics": [
        {{
            "name": "metric name",
            "description": "what it measures",
            "sql": "SQL expression e.g. COUNT(*) or SUM(amount)"
        }}
    ],
    "sample_data": "column_name: value1, value2, value3 | other_column: value1, value2, value3"
}}
Rules:
- Only include columns that actually exist in the columns list above
- Only suggest relationships if a column name strongly implies a FK (ends in _id)
- If nothing suggests a default filter, return an empty array
- For sample_data, list a few representative distinct values per column separated by pipes, e.g. "status: draft, posted | amount: 10.99, 249.00"
- Return only valid JSON, no explanation
"""
    model_config = get_model_config()
    response = client.chat.completions.create(**{
        "model": model_config['model'],
        "messages": [{"role": "user", "content": prompt}]
    })
    content = response.choices[0].message.content
    content = content.replace('```json', '').replace('```', '').strip()
    return json.loads(content)
def get_semantic_metrics():
    return st.session_state.get('semantic_metrics', [])
def save_semantic_metrics(metrics):
    st.session_state.semantic_metrics = metrics
def add_semantic_metadata(table):
    """Generate and save semantic metadata for selected table"""
    selected_tables = get_selected_tables()
    schema = table.get('schema', '')
    name = table.get('table', '')
    try:
        metadata = generate_semantic_metadata(table)
        # Merge into existing table info
        table['table_description']   = metadata.get('table_description', '')
        table['column_descriptions'] = metadata.get('column_descriptions', {})
        table['primary_key']         = metadata.get('primary_key', '')
        table['relationships']       = metadata.get('relationships', [])
        table['default_filters']     = metadata.get('default_filters', [])
        table['sample_data']         = metadata.get('sample_data', '')
        try:
            embedding_text = build_embedding_text(table)
            table['embedding'] = get_embedding(embedding_text)
            add_log(f"Embedding generated OK", f"{schema}.{name}")
        except Exception as e:
            add_log(f"Embedding failed", f"{schema}.{name}: {str(e)}")
        for i, t in enumerate(selected_tables):
            if t['schema'] == schema and t['table'] == name:
                selected_tables[i].update(table)
                break
        # Save common metrics globally
        existing_metrics = get_semantic_metrics()
        for metric in metadata.get('common_metrics', []):
            metric['source_table'] = f"{schema}.{name}"
            if not any(m['name'] == metric['name'] for m in existing_metrics):
                existing_metrics.append(metric)
        save_semantic_metrics(existing_metrics)
        add_log(f"Semantic metadata generated", f"{schema}.{name}: {json.dumps(metadata, indent=2)}")
    except Exception as e:
        add_log(f"Semantic metadata failed", f"{schema}.{name}: {str(e)}")
    save_selected_tables(selected_tables)
    add_log("all semantic metadata function completed", selected_tables)
def iter_llm_tokens(request):
    request = dict(request)
    stream_request = dict(request)
    stream_request["stream"] = True
    try:
        stream = client.chat.completions.create(**stream_request)
    except Exception:
        stream = None
    if stream is None:
        response = client.chat.completions.create(**request)
        yield response.choices[0].message.content
        return
    with stream:
        for event in stream:
            if event.choices:
                content = event.choices[0].delta.content
                if content:
                    yield content
def stream_llm_response(request, stream_target):
    chunks = []
    def generator():
        for token in iter_llm_tokens(request):
            chunks.append(token)
            yield token
    stream_target.write_stream(generator())
    return "".join(chunks)
def load_chat_history():
    return st.session_state.get('chat_history', {})
def save_chat_history(chat_history):
    st.session_state.chat_history = chat_history
def get_chat_title(messages):
    """Generate a title for the chat from the first message"""
    if messages and len(messages) > 0:
        first_message = messages[0].get("content", "New Chat")
        if isinstance(first_message, str):
            return first_message[:20] + "..." if len(first_message) > 20 else first_message
    return "New Chat"
def generate_chat_title(user_question, assistant_response):
    """Ask LLM to generate a short title for the chat"""
    try:
        title_prompt = f"Based on this conversation, generate a topic title in maximum 4 words. Only return the title, nothing else.\n\nUser: {user_question}\n\nAssistant response summary: {str(assistant_response)[:200]}"
        model_config = get_model_config()
        request = {
            "model": model_config['model'],
            "messages": [{"role": "user", "content": title_prompt}]
        }
        # Only add reasoning_effort for reasoning models
        if model_config['model'] in ['o1', 'o3', 'gpt-5'] and model_config['reasoning_effort']:
            request["reasoning_effort"] = model_config['reasoning_effort']
        response = client.chat.completions.create(**request)
        title = response.choices[0].message.content.strip()
        # Remove quotes if present and limit to 50 characters
        title = title.strip('"\'').strip()
        if len(title) > 50:
            title = title[:50]
        return title
    except Exception as e:
        # Fallback to simple title if generation fails
        return user_question[:30] + "..." if len(user_question) > 30 else user_question
def stream_generator(text):
    for word in text.split():
        yield word + " "
        time.sleep(0.05)
# modify this entire function
def get_data_prompt(question):
    prompt = """
        The user has a number of datasets, below is a list of all the tables with their columns.
        Based on the question of the user, write SQL SELECT queries to find answers in these tables.
        SQL dialect: {sql_dialect}
        Query Writing Rules
        Use column_types as the authoritative types; SESSION_DATE is already normalized to TIMESTAMP.
        For inclusive calendar ranges, use >= the start date and < the next day's or month's start.
        Never convert SESSION_DATE from Excel serial numbers again.
        Only use LIKE when the user is explicitly searching by a partial text value
        Never use LIKE for filtering on columns that have a fixed set of values, use = instead
        Use LIKE statements with % at the start and end if needed, to find relevant rows.
        Use LOWER() in the LIKE statements on both sides to make it case insensitive.
        Your answer should be a JSON with an array of SQL SELECT queries. For each item in the array,
        provide an object with these keys:
        - description: explains what part of the question is answered
        - query: the SQL SELECT query to execute
        - display_as_chart: boolean, true if the result would make sense to display as a chart
        - chart_type: suggest one of 'bar', 'line', 'pie', 'scatter'. Use 'bar' for comparisons/counts, 'line' for trends over time, 'pie' for distributions/proportions, 'scatter' for correlations. Empty string if display_as_chart is false.
        - chart_x_column: the column name for the x-axis (empty string if no chart)
        - chart_y_column: the column name for the y-axis or value (empty string if no chart)
        Semantic Reasoning Rules
        The user may use business terms that don't exactly match column names or values
        You must map user terms to the closest matching column name or column value
        Always check column descriptions to understand what values a column can contain
        If a column description mentions possible values (e.g. 'draft', 'posted'), use exact match (=) not LIKE
    """
    prompt = prompt.replace("{sql_dialect}", "DuckDB")
    custom_prompt = get_custom_prompt()
    if custom_prompt:
        prompt += f"\nAdditional Instructions:\n{custom_prompt}\n"
    selected_tables = get_relevant_tables(question) # will be get_relevant_tables(question) once RAG is added
    if selected_tables:
        prompt += "\n\nData Model:\n"
        for table in selected_tables:
            schema = table.get("schema", '')
            if schema != '':
                schema += "."
            name = table.get("table", '')
            # Exclude embedding from prompt
            table_for_prompt = {k: v for k, v in table.items() if k != 'embedding'}
            prompt += f"\n\n{schema}{name}\n{json.dumps(table_for_prompt, indent=2)}\n"
    else:
        prompt += "\n\nNo tables configured."
    prompt += f"\n\nUser question: {question}"
    return prompt
def get_chat_prompt(question):
    prompt = question
    custom_prompt = get_custom_prompt()
    if custom_prompt:
        prompt = prompt + "\n\n" + custom_prompt + "\n\n"
    return prompt
def is_data_prompt(question):
    is_data = ask_llm_simple(f"Answer with only 'True' or 'False'. Is this question asking about data, numbers, records, customers, sales, reports, or anything that would require looking up information in a database? Question: {question}")
    if is_data.strip() == "True":
        return True 
    return False
import re
def beautify_sql(query: str) -> str:
    # Normalize whitespace
    q = " ".join(query.strip().split())
    # Clause-level keywords (start on new line)
    clause_keywords = [
        "select", "from", "where", "group by", "order by",
        "having", "limit", "offset"
    ]
    # Join/condition keywords (stay inline but uppercase)
    inline_keywords = [
        "inner join", "left join", "right join", "join", "on"
    ]
    # Uppercase clause keywords with word boundaries
    for kw in sorted(clause_keywords, key=len, reverse=True):
        pattern = rf"\b{kw}\b"
        q = re.sub(pattern, "\n" + kw.upper(), q, flags=re.IGNORECASE)
    # Uppercase inline keywords with word boundaries (no line break)
    for kw in sorted(inline_keywords, key=len, reverse=True):
        pattern = rf"\b{kw}\b"
        q = re.sub(pattern, kw.upper(), q, flags=re.IGNORECASE)
    # Clean extra spaces around newlines
    q = re.sub(r"\n\s+", "\n", q)
    q = q.strip()
    # Format SELECT fields one per line if single-line comma separated
    if q.startswith("SELECT"):
        parts = q.split("\n", 1)
        header, body = parts[0], parts[1] if len(parts) > 1 else ""
        if header.count(",") > 0:
            fields = [f.strip() for f in header.replace("SELECT", "").split(",")]
            header = "SELECT\n    " + ",\n    ".join(fields)
        q = header + "\n" + body
    return q.strip()
def get_sql_explanation(query, description):
    """Ask LLM to explain the SQL query in plain English"""
    prompt = f"""Explain this SQL query in plain English for a non-technical user.
Query description: {description}
SQL query:
{query}
Provide a clear explanation covering:
1. Which tables are used and why
2. What filters are applied and what they mean
3. Any joins and why they are needed
4. What the query result will show
Keep it concise and avoid technical jargon. Use bullet points."""
    model_config = get_model_config()
    response = client.chat.completions.create(**{
        "model": model_config['model'],
        "messages": [{"role": "user", "content": prompt}]
    })
    return response.choices[0].message.content.strip()
def render_chart(df, chart_x_column, chart_y_column, selected_chart_type, toggle_key):
    """Render chart with smart integer y-axis handling"""
    y_values = pd.to_numeric(df[chart_y_column], errors='coerce').dropna()
    is_integer_data = len(y_values) > 0 and (y_values == y_values.round()).all()
    if is_integer_data:
        max_val = int(y_values.max())
        y_axis = alt.Y(chart_y_column, type="quantitative",
                      axis=alt.Axis(tickMinStep=1, format='d', values=list(range(0, max_val + 2))),
                      scale=alt.Scale(domain=[0, max_val + 1]))
    else:
        y_axis = alt.Y(chart_y_column, type="quantitative")
    if selected_chart_type == "bar":
        st.write(alt.Chart(df).mark_bar().encode(
            x=alt.X(chart_x_column, sort=None),
            y=y_axis))
    elif selected_chart_type == "line":
        st.write(alt.Chart(df).mark_line(point=True).encode(
            x=alt.X(chart_x_column, sort=None),
            y=y_axis))
    elif selected_chart_type == "scatter":
        st.write(alt.Chart(df).mark_point().encode(
            x=alt.X(chart_x_column, sort=None),
            y=y_axis))
    elif selected_chart_type == "pie":
        st.write(alt.Chart(df).mark_arc().encode(
            theta=alt.Theta(chart_y_column, type="quantitative"),
            color=alt.Color(chart_x_column, type="nominal")))
# Show message history
def show_message_history():
    add_log("Showing message history", f"{len(st.session_state.messages)} messages")
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            if isinstance(message["content"], list):
                for query_to_run in message["content"]:
                    description = query_to_run["description"]
                    st.text(description)
                    # Check if query failed
                    if query_to_run.get("failed", False):
                        query = query_to_run.get("query", "")
                        error = query_to_run.get("error", "Unknown error")
                        st.warning(f"Query could not be executed:\n```sql\n{query}\n```\nError: {error}")
                    elif "result_df" in query_to_run:
                        df = pd.read_json(StringIO(query_to_run["result_df"]))                        
                        display_as_chart = query_to_run.get("display_as_chart", False)
                        chart_type = query_to_run.get("chart_type", "bar")
                        chart_x_column = query_to_run.get("chart_x_column", "")
                        chart_y_column = query_to_run.get("chart_y_column", "")
                        query = query_to_run.get("query", "")
                        # Create unique key for this query result
                        toggle_key = f"view_toggle_history_{hash(str(query_to_run))}_{id(query_to_run)}"
                        # Determine if chart is available
                        has_chart = (display_as_chart and chart_x_column and chart_y_column and
                                    chart_x_column in df.columns and chart_y_column in df.columns)
                        # Show Chart/Table/SQL/Explanation if chart is available, otherwise just Table/SQL/Explanation
                        if has_chart:
                            view_mode = st.radio(
                                "View",
                                options=["Chart", "Table", "SQL", "Explanation"],
                                index=0,
                                key=toggle_key,
                                label_visibility="collapsed",
                                horizontal=True
                            )
                        else:
                            view_mode = st.radio(
                                "View",
                                options=["Table", "SQL", "Explanation"],
                                index=0,
                                key=toggle_key,
                                label_visibility="collapsed",
                                horizontal=True
                            )
                        if len(df) == 0:
                            st.info("No records found for this query. Try broadening your filters.")
                            formatted_query = beautify_sql(query)
                            st.code(formatted_query, language="sql")
                        else:
                            if view_mode == "Chart":
                                selected_chart_type = st.selectbox(
                                    "Chart type",
                                    options=["bar", "line", "pie", "scatter"],
                                    index=["bar", "line", "pie", "scatter"].index(chart_type) if chart_type in ["bar", "line", "pie", "scatter"] else 0,
                                    key=f"chart_type_history_{toggle_key}"
                                )
                                render_chart(df, chart_x_column, chart_y_column, selected_chart_type, toggle_key)
                            elif view_mode == "Table":
                                st.dataframe(df)
                            elif view_mode == "SQL":
                                formatted_query = beautify_sql(query)
                                st.code(formatted_query, language="sql")
                            elif view_mode == "Explanation":
                                with st.spinner("Generating explanation..."):
                                    explanation = get_sql_explanation(query, description)
                                st.markdown(explanation)
            else:
                st.markdown(message["content"])
def get_context():
    messages = []
    for message in st.session_state.messages:
        content = message["content"]
        role = message["role"]
        if isinstance(content, list):
            text = ""
            for query_to_run in content:
                text += query_to_run["description"] + ": " + query_to_run["query"] + ". "
            #add_log("Context text variable", f"Length: {len(text)} | Content: {text[:200]}")  #add this, checled with log (works)
            messages.append({"role": role, "content": text})
        else:
            messages.append({"role": role, "content": content})
    #add_log("Context being sent to LLM", json.dumps(messages, indent=2))  #the log (checked, works)
    return messages
def get_response_format():
    """Define the structured JSON schema for LLM responses"""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "query_response",
            "schema": {
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string"},
                                "description": {"type": "string"},
                                "display_as_chart": {"type": "boolean"},
                                "chart_type": {"type": "string", "enum": ["bar", "line", "pie", "scatter"]},
                                "chart_x_column": {"type": "string"},
                                "chart_y_column": {"type": "string"}
                            },
                            "required": [
                                "query", "description", "display_as_chart", "chart_type", "chart_x_column", "chart_y_column"
                            ],
                            "additionalProperties": False
                        }
                    }
                },
                "required": ["queries"],
                "additionalProperties": False
            },
            "strict": True
        }
    }
def ask_llm(messages, stream_target=None):
    model_config = get_model_config()
    request = {
        "model": model_config['model'],
        "messages": messages,
        "response_format": get_response_format()
    }
    # Only add reasoning_effort for reasoning models (o1, o3, gpt-5)
    if model_config['model'] in ['o1', 'o3', 'gpt-5'] and model_config['reasoning_effort']:
        request["reasoning_effort"] = model_config['reasoning_effort']
    if stream_target is None:
        response = client.chat.completions.create(**request)
        content = response.choices[0].message.content
    else:
        content = stream_llm_response(request, stream_target)
    content = content.replace('```json', '').replace('```', '')
    response_content = json.loads(content)
    return response_content
def ask_llm_simple(messages, stream_target=None):
    model_config = get_model_config()
    request = {
        "model": model_config['model'],
        "messages": [{"role": "user", "content": messages}]
    }
    if model_config['model'] in ['o1', 'o3', 'gpt-5'] and model_config['reasoning_effort']:
        request["reasoning_effort"] = model_config['reasoning_effort']
    response = client.chat.completions.create(**request)
    content = response.choices[0].message.content
    return content.strip()
def apply_styling():
    st.markdown(
        """
        <style>
            .stBottom {
                padding-bottom: 20px;
            }
            div:has(> [data-testid="stChatMessageAvatarUser"]) {
                margin-left: auto;
                width: 50%;
            }
        </style>
        """,
        unsafe_allow_html=True
    )
    st.markdown("""
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Asap:ital,wght@0,100..900;1,100..900&family=Roboto:ital,wght@0,100..900;1,100..900&display=swap" rel="stylesheet">
    <style>
        html, body, h1, h2, h3, h4, [class*="st-"]:not([data-testid="stIconMaterial"]), .main, .stApp {
            font-family: 'Roboto', sans-serif !important;
            font-size: 14px;
        }
        #stDecoration {
            display: none;
        }
        h1, .main-header {
            font-size: 1.5rem !important;
            color: #1f77b4;
            margin-bottom: 1rem;
        }
        .sub-header {
            font-size: 1.5rem;
            color: #1f77b4;
            margin-top: 1.5rem;
            margin-bottom: 1rem;
        }
        .stMainBlockContainer {
            padding-top: 1rem;
            padding-bottom: 1rem;
        }
        .stChatMessage {
            padding: 0rem;
        }
        button {
            background-color: transparent !important;
            border: none !important;
            text-align: left !important;
            justify-content: flex-start !important;
        }
        button:hover {
            background-color: transparent !important;
            border: none !important;
        }
        button:focus {
            background-color: transparent !important;
            border: none !important;
        }
        .stButton {
            height: 14px !important;
        }
        .stButton > button {
            background-color: transparent !important;
            border: none !important;
            text-align: left !important;
            justify-content: flex-start !important;
        }
        .stButton > button:hover {
            background-color: transparent !important;
            border: none !important;
        }
        button[kind="primary"] {
            color: black !important;
        }
        button[kind="primary"] p {
            font-weight: bold !important;
        }
        [data-testid="stSidebar"] .stButton > button {
            text-overflow: ellipsis !important;
            overflow: hidden !important;
            white-space: nowrap !important;
            display: block !important;
            width: 100% !important;
        }
    </style>
    """,
    unsafe_allow_html=True,
    )
def init():
    apply_styling()
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "current_chat_id" not in st.session_state:
        st.session_state.current_chat_id = None
    if "current_page" not in st.session_state:
        st.session_state.current_page = "chat"
def get_selected_tables():
    current_tables = get_data_model()
    if 'selected_tables' not in st.session_state:
        st.session_state.selected_tables = current_tables
    lookup = {(table['schema'], table['table']): table for table in current_tables}
    for table in st.session_state.selected_tables:
        current = lookup.get((table['schema'], table['table']))
        if current:
            table['columns'] = current['columns']
            table['column_types'] = current['column_types']
    return st.session_state.selected_tables

def save_selected_tables(selected_tables):
    st.session_state.selected_tables = selected_tables
def get_custom_prompt():
    return st.session_state.get('custom_prompt', '')
def save_custom_prompt(custom_prompt):
    st.session_state.custom_prompt = custom_prompt
def get_model_config():
    return st.session_state.get('model_config', {'model': 'gpt-4o-mini', 'reasoning_effort': 'medium'})
def save_model_config(model, reasoning_effort):
    st.session_state.model_config = {'model': model, 'reasoning_effort': reasoning_effort}
def get_logs():
    return st.session_state.get('logs', [])
def add_log(action, details):
    logs = list(get_logs())
    logs.append({'timestamp': datetime.now().isoformat(), 'action': action, 'details': details})
    st.session_state.logs = logs[-100:]
def clear_logs():
    st.session_state.logs = []
def add_table_with_columns(table_info):
    return dict(next(table for table in gold_tables if table['schema'] == table_info['schema'] and table['table'] == table_info['table']))
def setup_sidebar():
    """Setup the left sidebar with chat history and new chat button"""
    with st.sidebar:
        if st.button("New chat", use_container_width=True, icon=":material/edit_square:"):
            new_chat_id = str(uuid.uuid4())
            st.session_state.current_chat_id = new_chat_id
            st.session_state.messages = []
            st.session_state.current_page = "chat"
            st.rerun()
        if st.button("Settings", use_container_width=True, icon=":material/settings:"):
            st.session_state.current_page = "settings"
            st.rerun()
        st.divider()
        # Load and display chat history
        chat_history = load_chat_history()
        if chat_history:
            #st.subheader("History")
            # Sort by timestamp, newest first
            sorted_chats = sorted(
                chat_history.items(),
                key=lambda x: x[1].get("timestamp", 0),
                reverse=True
            )
            for chat_id, chat_data in sorted_chats:
                chat_title = chat_data.get("title", "Unnamed Chat")
                is_current = chat_id == st.session_state.current_chat_id
                col1, col2 = st.columns([0.85, 0.15])
                with col1:
                    if st.button(
                        chat_title,
                        key=f"chat_{chat_id}",
                        use_container_width=True,
                        type="primary" if is_current else "secondary"
                    ):
                        st.session_state.current_chat_id = chat_id
                        st.session_state.messages = chat_data.get("messages", [])
                        st.session_state.current_page = "chat"
                        st.rerun()
                with col2:
                    if st.button("", key=f"delete_{chat_id}", help="Delete chat", type="tertiary", icon=":material/delete:"):
                        del chat_history[chat_id]
                        save_chat_history(chat_history)
                        if st.session_state.current_chat_id == chat_id:
                            st.session_state.current_chat_id = None
                            st.session_state.messages = []
                        st.rerun()
        else:
            #st.info("No chats yet. Start a new one!")
            pass
def show_logs_page():
    """Display the logs page"""
    st.title("Logs")
    # Load logs
    logs = get_logs()
    # Add clear logs button
    col1, col2 = st.columns([0.8, 0.2])
    with col2:
        if st.button("Clear Logs", type="secondary"):
            clear_logs()
            st.rerun()
    st.divider()
    if logs:
        # Display logs in reverse order (newest first)
        for idx, log in enumerate(reversed(logs)):
            timestamp_iso = log.get('timestamp', '')
            action = log.get('action', '')
            details = log.get('details', '')
            # Format timestamp to show only HH:MM:SS
            try:
                dt = datetime.fromisoformat(timestamp_iso)
                timestamp = dt.strftime('%H:%M:%S')
            except:
                timestamp = timestamp_iso
            with st.expander(f"{timestamp} - {action}", expanded=False):
                st.text(details)
    else:
        st.info("No logs yet")
def show_settings_page():
    st.title("Settings")
    # Add link to logs
    if st.button("View Logs", icon=":material/article:"):
        st.session_state.current_page = "logs"
        st.rerun()
    st.divider()
    # Model configuration section
    st.write("**Model Configuration**")
    model_config = get_model_config()
    col1, col2 = st.columns(2)
    with col1:
        model = st.selectbox(
            "Model",
            options=["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "o1", "o3", "gpt-5"],
            index=["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "o1", "o3", "gpt-5"].index(model_config['model']) if model_config['model'] in ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "o1", "o3", "gpt-5"] else 0
        )
    with col2:
        reasoning_effort = st.selectbox(
            "Reasoning Effort (for reasoning models only)",
            options=["low", "medium", "high"],
            index=["low", "medium", "high"].index(model_config['reasoning_effort']) if model_config['reasoning_effort'] in ["low", "medium", "high"] else 1
        )
    # Save model config if changed
    if model != model_config['model'] or reasoning_effort != model_config['reasoning_effort']:
        save_model_config(model, reasoning_effort)
    st.divider()
    # Custom prompt section
    st.write("**Custom Instructions**")
    st.write("Add additional instructions to customize the AI's behavior:")
    custom_prompt = get_custom_prompt()
    custom_prompt_input = st.text_area(
        "Additional Prompt Instructions",
        value=custom_prompt,
        height=150,
        placeholder="Enter additional instructions for the AI (e.g., 'Always limit results to 10 rows', 'Format dates as YYYY-MM-DD', etc.)",
        label_visibility="collapsed"
    )
    if custom_prompt_input != custom_prompt:
        save_custom_prompt(custom_prompt_input)
    st.divider()
    # Get all available tables
    all_tables = get_data_model()
    # Get unique schemas (without db name)
    schemas = sorted(list(set([t['schema'] for t in all_tables])))
    # Load selected tables from state
    selected_tables = get_selected_tables()
    # Display currently selected tables
    st.write("**Included Tables**")
    if selected_tables:
        for idx, table in enumerate(selected_tables):
            col1, col2 = st.columns([0.9, 0.1])
            with col1:
                st.write(f"- {table['schema']}.{table['table']}")
            with col2:
                if st.button("", key=f"remove_{idx}", help="Remove table", icon=":material/delete:"):
                    selected_tables.pop(idx)
                    save_selected_tables(selected_tables)
                    st.rerun()
    else:
        st.info("No tables selected yet")
    st.divider()
    # Add new table section
    st.write("**Add New Table**")
    # Schema dropdown
    selected_schema = st.selectbox(
        "Select Schema",
        options=schemas,
        index=None,
        placeholder="Choose a schema..."
    )
    if selected_schema:
        # Filter tables by selected schema
        available_tables = [
            t for t in all_tables
            if t['schema'] == selected_schema
        ]
        # Table dropdown
        table_options = [t['table'] for t in available_tables]
        selected_table_name = st.selectbox(
            "Select Table",
            options=table_options,
            index=None,
            placeholder="Choose a table..."
        )
        if selected_table_name:
            # Find the full table info
            selected_table = next(
                (t for t in available_tables if t['table'] == selected_table_name),
                None
            )
            if st.button("Add Table", icon=":material/add:"):
                already_selected = any(
                    t['db'] == selected_table['db'] and
                    t['schema'] == selected_table['schema'] and
                    t['table'] == selected_table['table']
                    for t in selected_tables
                )
                if already_selected:
                    st.session_state.table_message = ("warning", "This table is already selected")
                else:
                    table_with_columns = add_table_with_columns(selected_table)
                    selected_tables.append(table_with_columns)
                    save_selected_tables(selected_tables)
                    add_semantic_metadata(table_with_columns)
                    st.session_state.table_message = ("success", f"Added {selected_schema}.{selected_table_name}")
                st.rerun()
            if "table_message" in st.session_state:
                msg_type, msg_text = st.session_state.table_message
                if msg_type == "success":
                    st.success(msg_text)
                else:
                    st.warning(msg_text)
def main():
    if st.session_state.current_page == "settings":
        show_settings_page()
    elif st.session_state.current_page == "logs":
        show_logs_page()
    else:
        st.title("AI Chatbot for company data")
        show_message_history()
        # Handle new chat input from user
        if question := st.chat_input("How can I help ?"):
            # Create new chat if none exists
            if st.session_state.current_chat_id is None:
                st.session_state.current_chat_id = str(uuid.uuid4())
                st.session_state.messages = []
            # Log user prompt
            add_log("Prompt from user", question)
            with st.chat_message("user"):
                st.markdown(question)
            with st.chat_message("assistant"):
                messages = get_context()
                stream_placeholder = st.empty()
                is_data = is_data_prompt(question)
                if is_data:
                    prompt = get_data_prompt(question)
                    messages.append({"role": "user", "content": prompt})
                    response_content = ask_llm(messages)
                else:
                    prompt = get_chat_prompt(question)
                    messages.append({"role": "user", "content": prompt})
                    response_content = ask_llm_simple(prompt)
                st.session_state.messages.append({"role": "user", "content": question})
                queries_to_run = ""
                # Clear the JSON streaming output
                stream_placeholder.empty()
                if is_data:
                    tries = 1
                    max_tries = 5
                    queries_to_run = process_and_display_queries(response_content)
                    with st.spinner("Running queries..."):
                        while tries < max_tries and any(query.get("failed") for query in queries_to_run):
                            tries += 1  
                            for query in queries_to_run:
                                if query.get("failed"):
                                    prompt += f"\n\n ATTEMPTED QUERY: {query["query"]} \nERROR: {query["error"]}"
                            messages.append({"role": "user", "content": prompt})
                            response_content = ask_llm(messages)
                            queries_to_run = process_and_display_queries(response_content, tries)
                    add_log("Amount of query retries", tries)
                else:
                    st.session_state.messages.append({"role": "assistant", "content": response_content})
                    st.write_stream(stream_generator(response_content))
                # Log final prompt sent to LLM
                add_log("Final prompt sent to LLM", prompt)
                # Log raw LLM answer
                add_log("Raw LLM answer", json.dumps(response_content, indent=2))
                # Save chat to history
                chat_history = load_chat_history()
                # Generate title using LLM for new chats (first exchange)
                if len(st.session_state.messages) == 2:  # First user message + first assistant response
                    chat_title = generate_chat_title(question, queries_to_run)
                else:
                    # Use existing title if already set, otherwise use get_chat_title fallback
                    existing_chat = chat_history.get(st.session_state.current_chat_id, {})
                    chat_title = existing_chat.get("title", get_chat_title(st.session_state.messages))
                chat_history[st.session_state.current_chat_id] = {
                    "title": chat_title,
                    "messages": st.session_state.messages,
                    "timestamp": datetime.now().timestamp()
                }
                save_chat_history(chat_history)
                st.rerun()
def process_and_display_queries(response_content, tries=1):
    if "queries" in response_content:
        queries_to_run = response_content["queries"]
    else:
        queries_to_run = response_content
            #st.write("These are the SQL queries generated by OpenAI:")
            #st.json(queries_to_run)
    add_log("Showing results", f"{len(queries_to_run)} queries to run")
    for query_to_run in queries_to_run:
        description = query_to_run["description"]
        query = query_to_run["query"]
        display_as_chart = query_to_run.get("display_as_chart", False)
        chart_type = query_to_run.get("chart_type", "bar")
        chart_x_column = query_to_run.get("chart_x_column", "")
        chart_y_column = query_to_run.get("chart_y_column", "")
        try:
            df = fetch_dataframe(query)
            df = df.loc[:,~df.columns.duplicated()].copy() # remove duplicate column names
            query_to_run["result_df"] = df.to_json()
            # Log successful query execution
            add_log("Query execution result", f"SUCCESS\nQuery: {query}\nRows returned: {len(df)}")
            #st.text(description)
            st.write_stream(stream_generator(description))
            #st.code(query, language = "SQL")
            # Create unique key for this query result
            toggle_key = f"view_toggle_{len(st.session_state.messages)}_{queries_to_run.index(query_to_run)}"
            # Determine if chart is available
            has_chart = (display_as_chart and chart_x_column and chart_y_column and
                        chart_x_column in df.columns and chart_y_column in df.columns)
            # Toggle buttons for view selection (right-aligned using CSS)
            st.markdown(f"""
                <style>
                div[data-testid="stHorizontalBlock"] div[data-testid="column"]:has(div.stRadio[data-testid*="{toggle_key}"]) {{
                    display: flex;
                    justify-content: flex-end;
                }}
                </style>
            """, unsafe_allow_html=True)
            col1, col2 = st.columns([1, 1])
            with col2:
                # Show Chart/Table/SQL/Explanation if chart is available, otherwise just Table/SQL/Explanation
                if has_chart:
                    view_mode = st.radio(
                        "View",
                        options=["Chart", "Table", "SQL", "Explanation"],
                        index=0,
                        key=toggle_key,
                        label_visibility="collapsed",
                        horizontal=True
                    )
                else:
                    view_mode = st.radio(
                        "View",
                        options=["Table", "SQL", "Explanation"],
                        index=0,
                        key=toggle_key,
                        label_visibility="collapsed",
                        horizontal=True
                    )
            if len(df) == 0:
                st.info("No records found for this query. Try broadening your filters.")
                formatted_query = beautify_sql(query)
                st.code(formatted_query, language="sql")
            elif view_mode == "Chart":
                selected_chart_type = st.selectbox(
                    "Chart type",
                    options=["bar", "line", "pie", "scatter"],
                    index=["bar", "line", "pie", "scatter"].index(chart_type) if chart_type in ["bar", "line", "pie", "scatter"] else 0,
                    key=f"chart_type_{toggle_key}"
                )
                render_chart(df, chart_x_column, chart_y_column, selected_chart_type, toggle_key)
            elif view_mode == "Table":
                st.dataframe(df)
            elif view_mode == "SQL":
                formatted_query = beautify_sql(query)
                st.code(formatted_query, language="sql")
            elif view_mode == "Explanation":
                with st.spinner("Generating explanation..."):
                    explanation = get_sql_explanation(query, description)
                st.markdown(explanation)
        except Exception as e:
            # Log failed query execution
            add_log("Query execution result", f"FAILED\nQuery: {query}\nError: {str(e)}")
            # Mark query as failed and store error info
            query_to_run["failed"] = True
            query_to_run["error"] = str(e)
            if tries == 5:
                st.warning(f"Query could not be executed:\n```sql\n{query}\n```\nError: {str(e)}")
    if tries == 5 or not any(query.get("failed") for query in queries_to_run): # 5 is retry limit
        st.session_state.messages.append({"role": "assistant", "content": queries_to_run})
    return queries_to_run
def get_embedding(text: str, model: str = "text-embedding-3-small"):
    request = {
        "input": text,
        "model": model
    }
    response = client.embeddings.create(**request)
    embedding = response.data[0].embedding
    return embedding
def build_embedding_text(table):
    """Build a single string summarising a table for embedding"""
    parts = [
        f"Table: {table.get('schema','')}.{table.get('table','')}",
        f"Description: {table.get('table_description', '')}",
        f"Columns: {', '.join(table.get('columns', []))}",
    ]
    col_desc = table.get('column_descriptions', {})
    if col_desc:
        parts.append("Column details: " + "; ".join(f"{k}: {v}" for k, v in col_desc.items()))
    sample = table.get('sample_data', '')
    if sample:
        parts.append(f"Sample data: {sample}")
    return "\n".join(parts)
def cosine_similarity(vec_a, vec_b):
    """Compute cosine similarity between two vectors"""
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    mag_a = math.sqrt(sum(a * a for a in vec_a))
    mag_b = math.sqrt(sum(b * b for b in vec_b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)
def get_relevant_tables(question, top_k=5):
    """Return the most relevant tables for a given question using embedding similarity"""
    selected_tables = get_selected_tables()
    tables_with_embeddings = [t for t in selected_tables if t.get('embedding')]
    if not tables_with_embeddings:
        add_log("RAG fallback", "No embeddings found, returning all tables")
        return selected_tables
    question_embedding = get_embedding(question)
    scored = []
    for table in tables_with_embeddings:
        score = cosine_similarity(question_embedding, table['embedding'])
        scored.append((score, table))
        add_log("RAG score", f"{table['schema']}.{table['table']}: {score:.4f}")
    scored.sort(key=lambda x: x[0], reverse=True)
    top_tables = [t for _, t in scored[:top_k]]
    add_log("RAG selected tables", [f"{t['schema']}.{t['table']}" for t in top_tables])
    return top_tables
init()
setup_sidebar()
main()
