#!/usr/bin/env python3


import logging
import os
import pathlib
import yaml
import sys
import random

import arango
import requests
from retrying import retry

LOGGER = logging.getLogger(__name__)
APP_API_URL = os.getenv('APP_API_URL', 'http://web_app:5000/api/')
DATA_FILE = pathlib.Path('test_data.yaml')
ARANGODB_ROOT_PASSWORD = os.getenv('ARANGODB_ROOT_PASSWORD')
ARANGODB_USER = os.getenv('ARANGODB_USER')
ARANGODB_PASSWORD = os.getenv('ARANGODB_PASSWORD')
ARANGODB_HOST = os.getenv('ARANGODB_HOST', 'test_db')
DB_NAME = 'quaerere'
COLLECTIONS = ['WebSites', 'WebPages']
TEST_DATA_MAP = {
    'WebSites': 'web_sites',
    'WebPages': 'web_pages',
}
TOKEN_MAP = {
    'WebSites': 'web-site',
    'WebPages': 'web-page',
}


class DBNotReadyException(Exception):
    pass


def retry_on_db_not_ready(exception):
    return isinstance(exception, DBNotReadyException)


def load_test_data():
    with open(DATA_FILE) as yaml_file:
        return yaml.load(yaml_file, yaml.BaseLoader)


@retry(wait_fixed=5000, stop_max_attempt_number=3)
def get_arango_conn():
    if ARANGODB_USER is None:
        LOGGER.error('Please set env variable "ARANGODB_USER"')
        sys.exit(1)
    a_client = arango.ArangoClient(
        protocol='http', host=ARANGODB_HOST, port=8529)
    LOGGER.info(f'Connecting to database {DB_NAME} on host {ARANGODB_HOST} '
                f'as {ARANGODB_USER}')
    db_conn = a_client.db(DB_NAME,
                          ARANGODB_USER,
                          ARANGODB_PASSWORD)

    res = db_conn.ping()
    if res != 200:
        raise IOError(f'Unable to connect to {DB_NAME}: {res}')

    return db_conn


def seed_collection(name, db_conn, data):
    collection = db_conn.collection(name)
    LOGGER.info(f'Adding seed data to {name}')
    for item in data:
        LOGGER.debug(f'Inserting: {data}')
        collection.insert(item)


def verify_collection(name, data, collection_conn):
    token = TOKEN_MAP[name]
    LOGGER.info(f'Performing "list" test on: {name}')
    verify_list(token, data)
    LOGGER.info(f'Performing "get" test on: {name}')
    for row in data:
        verify_get(token, row)
    LOGGER.info(f'Performing "delete" test on: {name}')
    deleted = verify_delete(token, random.choice(data), collection_conn)
    LOGGER.info(f'Performing "put" test on: {name}')
    verify_insert(token, deleted, collection_conn)


def verify_list(token, data):
    url = APP_API_URL + f'v1/{token}/'
    LOGGER.debug(f'Fetching list from: {url}')
    resp = requests.get(url)
    if not resp.ok:
        raise IOError(f'Could not get url: {resp.reason}')
    actual = resp.json()
    i = 0
    for row in data:
        if row != actual[i]:
            raise ValueError(
                f'Not equal; reference: {row}, actual: {actual[i]}')
        i += 1


def verify_get(token, data):
    key = data['_key']
    LOGGER.info(f'Fetching {key}')
    url = APP_API_URL + f'v1/{token}/{key}/'
    resp = requests.get(url)
    if not resp.ok:
        raise IOError(f'Could not get url: {resp.reason}')
    actual = resp.json()
    if actual != data:
        raise ValueError(f'Not equal; reference: {data}, actual: {actual}')


def verify_delete(token, data, collection):
    key = data['_key']
    LOGGER.info(f'Deleting {key}')
    url = APP_API_URL + f'v1/{token}/{key}/'
    del_resp = requests.delete(url)
    if not del_resp.ok:
        raise IOError(f'Could not delete item: {del_resp.reason}')
    resp = requests.get(url)
    if resp.ok:
        raise ValueError('Should not be able to get deleted item')
    found = None
    try:
        found = collection.get({'_key': key})
    except arango.DocumentGetError:
        return data
    except Exception as err:
        LOGGER.warning(f'Exception fallthrough: {err}')
    if found is not None:
        raise ValueError(f'Found deleted object in database: {found}')
    return data


def verify_insert(token, data, collection):
    LOGGER.info(f'Inserting: {data}')
    url = APP_API_URL + f'v1/{token}/'
    resp = requests.post(url, json=data)
    if not resp.ok:
        raise IOError(f'Could not insert item: {resp.reason}')
    resp_data = resp.json()
    actual = collection.get(resp_data)
    for meta in ['_id', '_rev']:
        actual.pop(meta)
    if actual != data:
        raise ValueError(f'Not equal; reference: {data}, actual: {actual}')


@retry(retry_on_exception=retry_on_db_not_ready,
       stop_max_attempt_number=7,
       wait_random_min=1500,
       wait_random_max=3000)
def verify_db_ready(db_conn):
    LOGGER.info('Verifying DB is ready')
    for collection in COLLECTIONS:
        LOGGER.debug(f'Verifying collection {collection} is available')
        if not db_conn.has_collection(collection):
            LOGGER.error(f'Missing the {collection} collection')
            raise DBNotReadyException


def setup_db():
    q_db = get_arango_conn()
    verify_db_ready(q_db)
    test_data = load_test_data()
    for collection in COLLECTIONS:
        collection_data = test_data[TEST_DATA_MAP[collection]]
        seed_collection(collection, q_db, collection_data)
        verify_collection(collection,
                          collection_data,
                          q_db.collection(collection))


def setup_logging():
    logging.basicConfig(level=logging.DEBUG)


def main():
    setup_logging()
    setup_db()


if __name__ == '__main__':
    main()
