#! /usr/bin/python3

"""
target_hash is the hash of a trigger.
"""

import binascii
import struct
import json
import logging
logger = logging.getLogger(__name__)

from counterpartylib.lib import (config, exceptions, util, message_type)
from . import (order, bet, rps)
from counterpartylib.lib.messages.triggers import (asset_metadata)

FORMAT = '>32s'
LENGTH = 32
ID = 120

receivers = [
    asset_metadata.asset_metadata_receiver,
]

def initialise (db):
    cursor = db.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS triggers(
                      tx_index INTEGER PRIMARY KEY,
                      tx_hash TEXT UNIQUE,
                      block_index INTEGER,
                      source TEXT,
                      target_hash TEXT,
                      payload BLOB,
                      status TEXT,
                      FOREIGN KEY (tx_index, tx_hash, block_index) REFERENCES transactions(tx_index, tx_hash, block_index))
                   ''')
                      # Offer hash is not a foreign key. (And it cannot be, because of some invalid triggers.)
    cursor.execute('''CREATE INDEX IF NOT EXISTS
                      block_index_idx ON triggers (block_index)
                   ''')
    cursor.execute('''CREATE INDEX IF NOT EXISTS
                      source_idx ON triggers (source)
                   ''')

    for receiver in receivers:
        receiver.initialise(db)

def validate (db, source, target_hash, payload_bytes):
    problems = []
    targets = []
    target_type = None

    cursor = db.cursor()
    receiver_instance = None
    for receiver in receivers:
        sql = 'SELECT tx_hash FROM ' + receiver.target_table_name() + ' WHERE tx_hash = ?'
        cursor.execute(sql, (target_hash,))
        targets = cursor.fetchall()
        if targets:
            assert receiver_instance is None
            assert len(targets) == 1
            receiver_instance = receiver(db, source, target_hash, payload_bytes)
            problems += receiver_instance.validate()

    if receiver_instance is None:
        problems.append('no trigger target with that hash')

    fee = int(0.001 * config.UNIT)
    cursor.execute('''SELECT * FROM balances
                      WHERE (address = ? AND asset = ?)''', (source, config.XCP))
    balances = cursor.fetchall()
    if not balances or balances[0]['quantity'] < fee:
        problems.append('insufficient funds')

    return receiver_instance, fee, problems

def compose (db, source, target_hash, payload, payload_is_hex):
    # convert memo to memo_bytes based on memo_is_hex setting
    if payload is None:
        payload_bytes = b''
    elif payload_is_hex:
        try:
            payload_bytes = bytes.fromhex(payload)
        except ValueError:
            raise exceptions.ComposeError(['failed to convert the payload'])

    else:
        payload_bytes = struct.pack(">{}s".format(len(payload)),
            payload.encode('utf-8'))

    # Check that target exists.
    _, _, problems = validate(db, source, target_hash, payload_bytes)
    if problems: raise exceptions.ComposeError(problems)

    target_hash_bytes = binascii.unhexlify(bytes(target_hash, 'utf-8'))

    data = message_type.pack(ID)
    data += struct.pack(FORMAT, target_hash_bytes)
    data += payload_bytes
    return (source, [], data)

def parse (db, tx, message):
    cursor = db.cursor()
    status = 'valid'

    if tx['block_hash'] == 'mempool':
        return

    # Unpack message.
    try:
        # account for memo bytes
        payload_bytes_length = len(message) - LENGTH
        if payload_bytes_length < 0:
            raise exceptions.UnpackError('invalid message length')

        struct_format = FORMAT + ('{}s'.format(payload_bytes_length))
        target_hash_bytes, payload_bytes = struct.unpack(struct_format, message)
        target_hash = binascii.hexlify(target_hash_bytes).decode('utf-8')
    except (exceptions.UnpackError, struct.error):
        target_hash = None
        payload_bytes = None
        status = 'invalid: could not unpack'

    if status == 'valid':
        receiver, fee, problems = validate(db, tx['source'], target_hash, payload_bytes)
        if problems:
            status = 'invalid: ' + '; '.join(problems)

    if status == 'valid':
        try:
            problems += receiver.execute(tx)
            if problems:
                status = 'invalid: ' + '; '.join(problems)
        except:
            status = 'invalid: execution failed'

    # Add parsed transaction to message-type–specific table.
    bindings = {
        'tx_index': tx['tx_index'],
        'tx_hash': tx['tx_hash'],
        'block_index': tx['block_index'],
        'source': tx['source'],
        'target_hash': target_hash,
        'payload': payload_bytes,
        'status': status,
    }
    if "integer overflow" not in status:
        sql='INSERT INTO triggers VALUES (:tx_index, :tx_hash, :block_index, :source, :target_hash, :payload, :status)'
        cursor.execute(sql, bindings)
    else:
        logger.warn("Not storing [trigger] tx [%s]: %s", tx['tx_hash'], status)
        logger.debug("Bindings: %s", json.dumps(bindings))

    if status == 'valid':
        util.debit(db, tx['source'], config.XCP, fee, action="trigger fee", event=tx['tx_hash'])

# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
