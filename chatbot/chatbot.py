VERSION = "2.2.2"

import json
import math
import os
import re
import time
import uuid
import pandas as pd
from datetime import datetime

from openai import AzureOpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
import altair as alt
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text

load_dotenv()

_token_provider = get_bearer_token_provider(
    DefaultAzureCredential(),
    "https://cognitiveservices.azure.com/.default"
)

openai_client = AzureOpenAI(
    azure_ad_token_provider=_token_provider,
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
)

_db_engine = None

def get_db_engine():
    global _db_engine
    if _db_engine is None:
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            st.error("Environment variable DATABASE_URL not set!")
            st.stop()
        _db_engine = create_engine(database_url)
    return _db_engine

STATE_FILE = "state.json"

def _load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def get_data_model():
    engine = get_db_engine()
    inspector = inspect(engine)
    all_tables = []
    for schema_name in inspector.get_schema_names():
        for table_name in inspector.get_table_names(schema=schema_name):
            all_tables.append({"schema": schema_name, "table": table_name})
    return all_tables


def generate_semantic_metadata(table_info):
    """Use LLM to generate semantic metadata for a table"""
    schema = table_info.get('schema', '')
    name = table_info.get('table', '')
    columns = table_info.get('columns', [])

    try:
        engine = get_db_engine()
        sample_df = pd.read_sql(
            text(f"SELECT * FROM {schema}.{name} LIMIT 5"),
            engine.connect()
        )
        sample_data = sample_df.to_string(index=False)
    except Exception:
        sample_data = "No sample data available"
        add_log("no sample data", f"{schema}.{name}")

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
    ]
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
    response = openai_client.chat.completions.create(
        model=model_config['model'],
        messages=[{"role": "user", "content": prompt}]
    )
    content = response.choices[0].message.content
    content = content.replace('```json', '').replace('```', '').strip()
    return json.loads(content)


def get_semantic_metrics():
    return (_load_state()).get('semantic_metrics', [])

def save_semantic_metrics(metrics):
    app_state = _load_state()
    app_state['semantic_metrics'] = metrics
    _save_state(app_state)

def add_semantic_metadata(table):
    """Generate and save semantic metadata for selected table"""
    selected_tables = get_selected_tables()
    schema = table.get('schema', '')
    name = table.get('table', '')

    try:
        metadata = generate_semantic_metadata(table)

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
    try:
        stream = openai_client.chat.completions.create(**request, stream=True)
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
    except Exception:
        response = openai_client.chat.completions.create(**request)
        yield response.choices[0].message.content


def stream_llm_response(request, stream_target):
    chunks = []

    def generator():
        for token in iter_llm_tokens(request):
            chunks.append(token)
            yield token

    stream_target.write_stream(generator())
    return "".join(chunks)


def load_chat_history():
    return (_load_state()).get('chat_history', {})

def save_chat_history(chat_history):
    app_state = _load_state()
    app_state['chat_history'] = chat_history
    _save_state(app_state)


def get_chat_title(messages):
    if messages and len(messages) > 0:
        first_message = messages[0].get("content", "New Chat")
        if isinstance(first_message, str):
            return first_message[:20] + "..." if len(first_message) > 20 else first_message
    return "New Chat"

def generate_chat_title(user_question, assistant_response):
    try:
        title_prompt = f"Based on this conversation, generate a topic title in maximum 4 words. Only return the title, nothing else.\n\nUser: {user_question}\n\nAssistant response summary: {str(assistant_response)[:200]}"

        model_config = get_model_config()
        request = {
            "model": model_config['model'],
            "messages": [{"role": "user", "content": title_prompt}]
        }
        if model_config['model'] in ['o1', 'o3', 'gpt-5'] and model_config['reasoning_effort']:
            request["reasoning_effort"] = model_config['reasoning_effort']

        response = openai_client.chat.completions.create(**request)
        title = response.choices[0].message.content.strip().strip('"\'')
        return title[:50] if len(title) > 50 else title
    except Exception:
        return user_question[:30] + "..." if len(user_question) > 30 else user_question


def stream_generator(text):
    for word in text.split():
        yield word + " "
        time.sleep(0.05)


def get_data_prompt(question):
    prompt = """
        The user has a number of datasets, below is a list of all the tables with their columns.
        Based on the question of the user, write SQL SELECT queries to find answers in these tables.

        Query Writing Rules
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
    custom_prompt = get_custom_prompt()
    if custom_prompt:
        prompt += f"\nAdditional Instructions:\n{custom_prompt}\n"

    selected_tables = get_relevant_tables(question)

    if selected_tables:
        prompt += "\n\nData Model:\n"
        for table in selected_tables:
            schema = table.get("schema", '')
            if schema:
                schema += "."
            name = table.get("table", '')
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
    return is_data.strip() == "True"


def beautify_sql(query: str) -> str:
    q = " ".join(query.strip().split())

    clause_keywords = ["select", "from", "where", "group by", "order by", "having", "limit", "offset"]
    inline_keywords = ["inner join", "left join", "right join", "join", "on"]

    for kw in sorted(clause_keywords, key=len, reverse=True):
        q = re.sub(rf"\b{kw}\b", "\n" + kw.upper(), q, flags=re.IGNORECASE)

    for kw in sorted(inline_keywords, key=len, reverse=True):
        q = re.sub(rf"\b{kw}\b", kw.upper(), q, flags=re.IGNORECASE)

    q = re.sub(r"\n\s+", "\n", q).strip()

    if q.startswith("SELECT"):
        parts = q.split("\n", 1)
        header, body = parts[0], parts[1]
        if header.count(",") > 0:
            fields = [f.strip() for f in header.replace("SELECT", "").split(",")]
            header = "SELECT\n    " + ",\n    ".join(fields)
        q = header + "\n" + body

    return q.strip()


def get_sql_explanation(query, description):
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
    response = openai_client.chat.completions.create(
        model=model_config['model'],
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content.strip()


def render_chart(df, chart_x_column, chart_y_column, selected_chart_type, toggle_key):
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
        st.write(alt.Chart(df).mark_bar().encode(x=alt.X(chart_x_column, sort=None), y=y_axis))
    elif selected_chart_type == "line":
        st.write(alt.Chart(df).mark_line(point=True).encode(x=alt.X(chart_x_column, sort=None), y=y_axis))
    elif selected_chart_type == "scatter":
        st.write(alt.Chart(df).mark_point().encode(x=alt.X(chart_x_column, sort=None), y=y_axis))
    elif selected_chart_type == "pie":
        st.write(alt.Chart(df).mark_arc().encode(
            theta=alt.Theta(chart_y_column, type="quantitative"),
            color=alt.Color(chart_x_column, type="nominal")))


def show_message_history():
    add_log("Showing message history", f"{len(st.session_state.messages)} messages")
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            if isinstance(message["content"], list):
                for query_to_run in message["content"]:
                    description = query_to_run["description"]
                    st.text(description)

                    if query_to_run.get("failed", False):
                        query = query_to_run.get("query", "")
                        error = query_to_run.get("error", "Unknown error")
                        st.warning(f"Query could not be executed:\n```sql\n{query}\n```\nError: {error}")
                    elif "result_df" in query_to_run:
                        df = pd.read_json(query_to_run["result_df"])

                        display_as_chart = query_to_run.get("display_as_chart", False)
                        chart_type = query_to_run.get("chart_type", "bar")
                        chart_x_column = query_to_run.get("chart_x_column", "")
                        chart_y_column = query_to_run.get("chart_y_column", "")
                        query = query_to_run.get("query", "")

                        toggle_key = f"view_toggle_history_{hash(str(query_to_run))}_{id(query_to_run)}"

                        has_chart = (display_as_chart and chart_x_column and chart_y_column and
                                    chart_x_column in df.columns and chart_y_column in df.columns)

                        if has_chart:
                            view_mode = st.radio("View", options=["Chart", "Table", "SQL", "Explanation"],
                                                 index=0, key=toggle_key, label_visibility="collapsed", horizontal=True)
                        else:
                            view_mode = st.radio("View", options=["Table", "SQL", "Explanation"],
                                                 index=0, key=toggle_key, label_visibility="collapsed", horizontal=True)

                        if len(df) == 0:
                            st.info("No records found for this query. Try broadening your filters.")
                            st.code(beautify_sql(query), language="sql")
                        else:
                            if view_mode == "Chart":
                                selected_chart_type = st.selectbox(
                                    "Chart type", options=["bar", "line", "pie", "scatter"],
                                    index=["bar", "line", "pie", "scatter"].index(chart_type) if chart_type in ["bar", "line", "pie", "scatter"] else 0,
                                    key=f"chart_type_history_{toggle_key}"
                                )
                                render_chart(df, chart_x_column, chart_y_column, selected_chart_type, toggle_key)
                            elif view_mode == "Table":
                                st.dataframe(df)
                            elif view_mode == "SQL":
                                st.code(beautify_sql(query), language="sql")
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
            text_parts = ""
            for query_to_run in content:
                text_parts += query_to_run["description"] + ": " + query_to_run["query"] + ". "
            messages.append({"role": role, "content": text_parts})
        else:
            messages.append({"role": role, "content": content})
    return messages


def get_response_format():
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
                            "required": ["query", "description", "display_as_chart", "chart_type", "chart_x_column", "chart_y_column"],
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
    if model_config['model'] in ['o1', 'o3', 'gpt-5'] and model_config['reasoning_effort']:
        request["reasoning_effort"] = model_config['reasoning_effort']

    if stream_target is None:
        response = openai_client.chat.completions.create(**request)
        content = response.choices[0].message.content
    else:
        content = stream_llm_response(request, stream_target)

    content = content.replace('```json', '').replace('```', '')
    return json.loads(content)


def ask_llm_simple(messages, stream_target=None):
    model_config = get_model_config()
    request = {
        "model": model_config['model'],
        "messages": [{"role": "user", "content": messages}]
    }
    if model_config['model'] in ['o1', 'o3', 'gpt-5'] and model_config['reasoning_effort']:
        request["reasoning_effort"] = model_config['reasoning_effort']

    response = openai_client.chat.completions.create(**request)
    return response.choices[0].message.content.strip()


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
    st.set_page_config(layout="wide")

    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "current_chat_id" not in st.session_state:
        st.session_state.current_chat_id = None
    if "current_page" not in st.session_state:
        st.session_state.current_page = "chat"


def get_selected_tables():
    return (_load_state()).get('selected_tables', [])

def save_selected_tables(selected_tables):
    app_state = _load_state()
    app_state['selected_tables'] = selected_tables
    _save_state(app_state)


def get_custom_prompt():
    return (_load_state()).get('custom_prompt', '')

def save_custom_prompt(custom_prompt):
    app_state = _load_state()
    app_state['custom_prompt'] = custom_prompt
    _save_state(app_state)


def get_model_config():
    return (_load_state()).get('model_config', {'model': 'gpt-4o-mini', 'reasoning_effort': 'medium'})

def save_model_config(model, reasoning_effort):
    app_state = _load_state()
    app_state['model_config'] = {'model': model, 'reasoning_effort': reasoning_effort}
    _save_state(app_state)


def get_logs():
    return (_load_state()).get('logs', [])

def add_log(action, details):
    app_state = _load_state()
    logs = app_state.get('logs', [])
    logs.append({'timestamp': datetime.now().isoformat(), 'action': action, 'details': details})
    if len(logs) > 100:
        logs = logs[-100:]
    app_state['logs'] = logs
    _save_state(app_state)

def clear_logs():
    app_state = _load_state()
    app_state['logs'] = []
    _save_state(app_state)


def add_table_with_columns(table_info):
    engine = get_db_engine()
    inspector = inspect(engine)
    columns = inspector.get_columns(table_info["table"], schema=table_info["schema"])
    table_info["columns"] = [col["name"] for col in columns if not col["name"].startswith("_sdc")]
    return table_info


def setup_sidebar():
    with st.sidebar:
        if st.button("New chat", use_container_width=True, icon=":material/edit_square:"):
            st.session_state.current_chat_id = str(uuid.uuid4())
            st.session_state.messages = []
            st.session_state.current_page = "chat"
            st.rerun()

        if st.button("Settings", use_container_width=True, icon=":material/settings:"):
            st.session_state.current_page = "settings"
            st.rerun()

        st.divider()

        chat_history = load_chat_history()

        if chat_history:
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
                    if st.button(chat_title, key=f"chat_{chat_id}", use_container_width=True,
                                 type="primary" if is_current else "secondary"):
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


def show_logs_page():
    st.title("Logs")

    logs = get_logs()
    col1, col2 = st.columns([0.8, 0.2])
    with col2:
        if st.button("Clear Logs", type="secondary"):
            clear_logs()
            st.rerun()

    st.divider()

    if logs:
        for log in reversed(logs):
            timestamp_iso = log.get('timestamp', '')
            action = log.get('action', '')
            details = log.get('details', '')
            try:
                timestamp = datetime.fromisoformat(timestamp_iso).strftime('%H:%M:%S')
            except Exception:
                timestamp = timestamp_iso

            with st.expander(f"{timestamp} - {action}", expanded=False):
                st.text(details)
    else:
        st.info("No logs yet")


def show_settings_page():
    st.title("Settings")
    if st.button("View Logs", icon=":material/article:"):
        st.session_state.current_page = "logs"
        st.rerun()

    st.divider()

    st.write("**Model Configuration**")
    model_config = get_model_config()
    col1, col2 = st.columns(2)

    with col1:
        model_options = ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "o1", "o3", "gpt-5"]
        model = st.selectbox(
            "Model",
            options=model_options,
            index=model_options.index(model_config['model']) if model_config['model'] in model_options else 0
        )

    with col2:
        effort_options = ["low", "medium", "high"]
        reasoning_effort = st.selectbox(
            "Reasoning Effort (for reasoning models only)",
            options=effort_options,
            index=effort_options.index(model_config['reasoning_effort']) if model_config['reasoning_effort'] in effort_options else 1
        )

    if model != model_config['model'] or reasoning_effort != model_config['reasoning_effort']:
        save_model_config(model, reasoning_effort)

    st.divider()

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

    all_tables = get_data_model()
    schemas = sorted(list(set([t['schema'] for t in all_tables])))
    selected_tables = get_selected_tables()

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

    st.write("**Add New Table**")

    selected_schema = st.selectbox("Select Schema", options=schemas, index=None, placeholder="Choose a schema...")

    if selected_schema:
        table_options = [t['table'] for t in all_tables if t['schema'] == selected_schema]
        selected_table_name = st.selectbox("Select Table", options=table_options, index=None, placeholder="Choose a table...")

        if selected_table_name:
            selected_table = next((t for t in all_tables if t['schema'] == selected_schema and t['table'] == selected_table_name), None)

            if st.button("Add Table", icon=":material/add:"):
                already_selected = any(
                    t['schema'] == selected_table['schema'] and t['table'] == selected_table['table']
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

        if question := st.chat_input("How can I help ?"):
            if st.session_state.current_chat_id is None:
                st.session_state.current_chat_id = str(uuid.uuid4())
                st.session_state.messages = []

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
                                    prompt += f"\n\n ATTEMPTED QUERY: {query['query']} \nERROR: {query['error']}"
                            messages.append({"role": "user", "content": prompt})
                            response_content = ask_llm(messages)
                            queries_to_run = process_and_display_queries(response_content, tries)

                    add_log("Amount of query retries", tries)
                else:
                    st.session_state.messages.append({"role": "assistant", "content": response_content})
                    st.write_stream(stream_generator(response_content))

                add_log("Final prompt sent to LLM", prompt)
                add_log("Raw LLM answer", json.dumps(response_content, indent=2))

                chat_history = load_chat_history()

                if len(st.session_state.messages) == 2:
                    chat_title = generate_chat_title(question, queries_to_run)
                else:
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
    queries_to_run = response_content.get("queries", response_content)

    add_log("Showing results", f"{len(queries_to_run)} queries to run")

    engine = get_db_engine()

    for query_to_run in queries_to_run:
        description = query_to_run["description"]
        query = query_to_run["query"]
        display_as_chart = query_to_run.get("display_as_chart", False)
        chart_type = query_to_run.get("chart_type", "bar")
        chart_x_column = query_to_run.get("chart_x_column", "")
        chart_y_column = query_to_run.get("chart_y_column", "")

        try:
            with engine.connect() as conn:
                df = pd.read_sql(text(query.replace("\n", " ")), conn)
            df = df.loc[:, ~df.columns.duplicated()].copy()
            query_to_run["result_df"] = df.to_json()

            add_log("Query execution result", f"SUCCESS\nQuery: {query}\nRows returned: {len(df)}")

            st.write_stream(stream_generator(description))

            toggle_key = f"view_toggle_{len(st.session_state.messages)}_{queries_to_run.index(query_to_run)}"

            has_chart = (display_as_chart and chart_x_column and chart_y_column and
                        chart_x_column in df.columns and chart_y_column in df.columns)

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
                if has_chart:
                    view_mode = st.radio("View", options=["Chart", "Table", "SQL", "Explanation"],
                                         index=0, key=toggle_key, label_visibility="collapsed", horizontal=True)
                else:
                    view_mode = st.radio("View", options=["Table", "SQL", "Explanation"],
                                         index=0, key=toggle_key, label_visibility="collapsed", horizontal=True)

            if len(df) == 0:
                st.info("No records found for this query. Try broadening your filters.")
                st.code(beautify_sql(query), language="sql")
            elif view_mode == "Chart":
                selected_chart_type = st.selectbox(
                    "Chart type", options=["bar", "line", "pie", "scatter"],
                    index=["bar", "line", "pie", "scatter"].index(chart_type) if chart_type in ["bar", "line", "pie", "scatter"] else 0,
                    key=f"chart_type_{toggle_key}"
                )
                render_chart(df, chart_x_column, chart_y_column, selected_chart_type, toggle_key)
            elif view_mode == "Table":
                st.dataframe(df)
            elif view_mode == "SQL":
                st.code(beautify_sql(query), language="sql")
            elif view_mode == "Explanation":
                with st.spinner("Generating explanation..."):
                    explanation = get_sql_explanation(query, description)
                st.markdown(explanation)

        except Exception as e:
            add_log("Query execution result", f"FAILED\nQuery: {query}\nError: {str(e)}")
            query_to_run["failed"] = True
            query_to_run["error"] = str(e)
            if tries == 5:
                st.warning(f"Query could not be executed:\n```sql\n{query}\n```\nError: {str(e)}")

    if tries == 5 or not any(query.get("failed") for query in queries_to_run):
        st.session_state.messages.append({"role": "assistant", "content": queries_to_run})

    return queries_to_run


def get_embedding(text: str, model: str = "text-embedding-3-small"):
    response = openai_client.embeddings.create(input=text, model=model)
    return response.data[0].embedding


def build_embedding_text(table):
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
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    mag_a = math.sqrt(sum(a * a for a in vec_a))
    mag_b = math.sqrt(sum(b * b for b in vec_b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def get_relevant_tables(question, top_k=5):
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
