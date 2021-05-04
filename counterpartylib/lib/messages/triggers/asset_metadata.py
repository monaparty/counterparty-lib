""" 'trigger module' for asset_metadata """

import bson
import struct
import marshal
import logging
logger = logging.getLogger(__name__)

from counterpartylib.lib.messages import (issuance)
from counterpartylib.lib.messages.triggers import (trigger_receiver)
FORMAT = '>B'
LENGTH = 1

TYPE_STORE      = 1
TYPE_LOCK       = 2
TYPE_STORE_LOCK = 3
TYPE_DELETE     = 4

class asset_metadata_receiver(trigger_receiver):
    __db = None
    __asset = ''
    __source = None
    __target_hash = ''
    __payload_bytes = b''

    @classmethod
    def initialise (cls, db):
        with db:
            cursor = db.cursor()
            cursor.execute('''CREATE TABLE IF NOT EXISTS asset_metadatas(
                        tx_index INTEGER PRIMARY KEY,
                        tx_hash TEXT UNIQUE,
                        block_index INTEGER,
                        message_index INTEGER,
                        asset TEXT,
                        key TEXT,
                        payload BLOB,
                        locked BOOLEAN,
                        FOREIGN KEY (tx_index, tx_hash, block_index) REFERENCES transactions(tx_index, tx_hash, block_index))
                        ''')

    def __init__(self, db, source, target_hash, payload_bytes):
        self.__db = db
        self.__source = source
        self.__target_hash = target_hash
        self.__payload_bytes = payload_bytes

    @property
    def name(self):
        return 'asset_metadata'

    @classmethod
    def target_table_name(cls):
        return 'issuances'

    def __unpack(self, payload_bytes):
        length = len(payload_bytes) - LENGTH
        if length <= 0:
            return None, None, ['invalid message length']
        struct_format = FORMAT + ('{}s'.format(length))
        query_type, bson_bytes = struct.unpack(struct_format, payload_bytes)
        bson_object = bson.loads(bson_bytes)

        return query_type, bson_object, []

    @classmethod
    def compose (cls, db, source, target_hash, payload, payload_is_hex):
        pass

    def validate (self):
        try:
            query_type, bson_object, problems = self.__unpack(self.__payload_bytes)
        except:
            return ['failed to parse payload']

        asset = issuance.find_issuance_by_tx_hash(self.__db, self.__target_hash)
        if asset is None:
            problems.append('Cannot find target asset')

        if not problems:
            with self.__db:
                problems += self.__query(None, asset, query_type, bson_object)

        return problems

    def execute (self, tx):
        query_type, bson_object, problems = self.__unpack(self.__payload_bytes)

        asset = issuance.find_issuance_by_tx_hash(self.__db, self.__target_hash)
        if asset is None:
            problems.append('Cannot find target asset')

        if not problems:
            try:
                with self.__db:
                    problems += self.__query(tx, asset, query_type, bson_object)
                    if problems:
                        raise Exception() # make DB transactions rolloback
            except Exception as e:
                logging.critical(e, exc_info=True)
                if len(problems) == 0:
                    problems.append('Database related error')

        return problems

    def __query(self, tx, asset, query_type, bson_object):
        problems = []

        if query_type == TYPE_STORE:
            problems += self.__store(tx, asset, bson_object)
        elif query_type == TYPE_LOCK:
            problems += self.__lock(tx, asset, bson_object)
        elif query_type == TYPE_STORE_LOCK:
            problems += self.__store(tx, asset, bson_object)
            problems += self.__lock(tx, asset, bson_object)
        elif query_type == TYPE_DELETE:
            problems += self.__store(tx, asset, None)
        elif dry_run:
            problems += 'Unknown query type'
        else:
            assert False, 'Seems bugs in the validation phase.'

        return problems


    def __store(self, tx, asset, bson_object):
        problems = []
        cursor = self.__db.cursor()

        for key in bson_object.keys():
            cursor.execute('''SELECT * FROM asset_metadatas
                WHERE asset = ? AND key = ? AND locked = TRUE''', (asset, key))
            if list(cursor):
                problems.append('the key "{}" bound to the asset "{}" has been locked'.format(key, asset))

        if not problems and tx:
            cursor = self.__db.cursor()
            for key, value in bson_object.items():
                bindings = {
                    'tx_index': tx['tx_index'],
                    'tx_hash': tx['tx_hash'],
                    'block_index': tx['block_index'],
                    'message_index': 0,
                    'asset': asset,
                    'key': key,
                    'payload': marshal.dumps(value),
                    'locked': False
                }
                cursor.execute('''INSERT INTO asset_metadatas VALUES (
                    :tx_index, :tx_hash, :block_index, :message_index,
                    :asset, :key, :payload, :locked)''', bindings)

        return problems

    def __lock(self, tx, asset, bson_object):
        problems = []

        cursor = self.__db.cursor()
        for key in bson_object.items():
            cursor.execute('''SELECT * FROM asset_metadatas
                            WHERE (asset = ? AND key = ?)
                            ORDER BY tx_index ASC''', (asset, key))
            metadatas = cursor.fetchall()
            metadata = metadatas[-1]
            if metadata['locked']:
                problems.append('the key "{}" bound to the asset "{}" has been locked'.format(key, asset))
            elif tx:
                bindings = {
                    'tx_index': tx['tx_index'],
                    'tx_hash': tx['tx_hash'],
                    'block_index': tx['block_index'],
                    'message_index': 0,
                    'asset': metadata['asset'],
                    'key': metadata['key'],
                    'payload': marshal.dumps(bson_object),
                    'locked': True
                }
                cursor.execute('''INSERT INTO asset_metadatas VALUES (
                    :tx_index, :tx_hash, :block_index, :message_index,
                    :asset, :key, :payload, :locked)''', bindings)
