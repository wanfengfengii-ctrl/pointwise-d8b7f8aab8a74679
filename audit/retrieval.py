import sqlite3

from signing.commit import read_signed_result
from signing.results import SignedResult


def retrieve_result(db: sqlite3.Connection, result_id: int) -> SignedResult | None:
    return read_signed_result(db, result_id)
