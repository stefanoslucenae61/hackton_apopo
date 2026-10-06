from pathlib import Path
import pandas as pd

folder = Path("data/bronze")
source = folder / "data_bronze.xlsb"

with pd.ExcelFile(source, engine="pyxlsb") as workbook:
    for index, sheet in enumerate(workbook.sheet_names, start=1):
        df = workbook.parse(sheet)
        output = folder / f"sheet_{index}.parquet"
        df.to_parquet(output, index=False)
        print(f"{sheet} → {output}")