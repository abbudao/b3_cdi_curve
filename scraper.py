from bs4 import BeautifulSoup
from datetime import datetime
from datetime import date
from datetime import timedelta
import math
import numpy as np
import os.path
import os
import pandas as pd
from pandas.tseries.offsets import BDay
import sqlite3
import requests
from typing import Tuple
import time

initial_date = date(year=2003, month=8, day=8)
latest_date = date.today() - BDay(1)
output_dir = "./output/"


def save_dataframe(df: pd.DataFrame, df_name: str):

    df_path = output_dir + df_name
    df.to_csv(df_path, index=False, compression="gzip")
    print("Saved file: " + df_path)


def try_get_local_dataframe(df_name: str) -> Tuple[bool, pd.DataFrame]:

    filepath = output_dir + df_name
    if os.path.isfile(filepath):
        return (
            True,
            pd.read_csv(filepath, compression="gzip").astype(
                {"duration": int, "252": float, "360": float}
            ),
        )
    return (False, None)


def parse_html(html_content: str) -> Tuple[bool, pd.DataFrame]:

    soup = BeautifulSoup(html_content, "html5lib")
    table = soup.find("table", attrs={"id": "tb_principal1"})

    if table is None:
        return (False, None)

    dtypes = np.dtype(
        [
            ("duration", int),
            ("252", float),
            ("360", float),
        ]
    )
    data = np.empty(0, dtype=dtypes)
    df = pd.DataFrame(data)

    for i, tr in enumerate(table.find_all("tr")[2:]):
        row = []
        for td in tr.find_all("td"):
            content = td.text
            if "," in content:
                row.append(float(content.replace(".", "").replace(",", ".")))
            else:
                row.append(int(content))
        df.loc[i] = row

    df_full = pd.DataFrame(
        {
            "duration": pd.Series(
                np.array(list(range(1, int(df["duration"].iloc[-1]) + 1)))
            ).astype(int)
        }
    )

    df_full = df_full.merge(df, on="duration", how="left")
    df_full.interpolate(method="polynomial", order=3, inplace=True)

    return (True, df_full)


def get_db_connection() -> sqlite3.Connection:

    filepath = output_dir + "cdi.db"
    conn = sqlite3.connect(filepath)
    db_cursor = conn.cursor()
    db_cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='cdi';"
    )

    if not db_cursor.fetchone():
        conn.execute(
            "CREATE TABLE cdi (date TEXT, value REAL, base INTEGER, duration INTEGER);"
        )

    db_cursor.close()
    return conn


def get_latest_date(db_conn: sqlite3.Connection) -> Tuple[bool, pd.Timestamp]:

    db_cursor = db_conn.cursor()
    db_cursor.execute("SELECT date FROM cdi ORDER BY date DESC LIMIT 1;")

    result = db_cursor.fetchone()
    db_cursor.close()

    if len(result) == 0:
        return (False, None)

    return (True, pd.Timestamp(datetime.strptime(result[0], "%Y%m%d"), tz=None))


def update_db(db_conn: sqlite3.Connection, save_all_files: bool):

    print("start: update_db(_, save_all_files:" + str(save_all_files) + ")")

    db_has_entries, last_db_date = get_latest_date(db_conn)

    if db_has_entries:
        start_date = last_db_date + BDay(1)
    else:
        start_date = initial_date

    end_date = latest_date

    while start_date <= end_date:

        filename = "CDI_Curve_" + start_date.strftime("%Y%m%d") + ".gzip"
        has_local_file, local_df = try_get_local_dataframe(filename)
        if has_local_file:
            upsert_data(start_date, local_df, db_conn)
            start_date = start_date + BDay(1)
            continue

        url = (
            "http://www2.bmf.com.br/pages/portal/bmfbovespa/lumis/lum-taxas-referenciais-bmf-ptBR.asp?Data="
            + start_date.strftime("%d/%m/%Y")
            + "&slcTaxa=PRE"
        )

        html_content = requests.post(url).text

        if "Não há dados para a data" in html_content:
            start_date = start_date + BDay(1)
            continue

        success_parsing, df = parse_html(html_content)

        if not success_parsing:
            start_date = start_date + BDay(1)
            continue

        upsert_data(start_date, df, db_conn)

        if save_all_files:
            save_dataframe(df, filename)

        start_date = start_date + BDay(1)

        # Good practice to avoid flooding others with requests, remove at your own risk
        time.sleep(2)

    db_conn.commit()
    print("end: save_all_files()")


def upsert_data(dt: pd.Timestamp, df: pd.DataFrame, db_conn: sqlite3.Connection):

    db_cursor = db_conn.cursor()

    bases = [252, 360]
    date = dt.strftime("%Y-%m-%d")
    for base in bases:
        for index, row in df.iterrows():
            val = row[str(base)]
            if math.isnan(val):
                continue

            doc = {
                "date": f"'{date}'",
                "base": str(base),
                "duration": str(int(row["duration"])),
                "value": str(float(row[str(base)])),
            }

            columns = ", ".join(doc.keys())
            values = ", ".join(doc.values())
            query = f"INSERT INTO cdi ({columns}) VALUES ({values});"
            db_cursor.execute(query)

    db_cursor.close()


print("> start script")

if not os.path.isdir(output_dir):
    os.mkdir(output_dir)

db_conn = get_db_connection()
update_db(db_conn, False)
db_conn.close()

print("> end")