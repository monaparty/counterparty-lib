#! /usr/bin/python3

import os
import hashlib
import binascii
import time
import sqlite3
import appdirs
import logging
import decimal
D = decimal.Decimal

from lib import (config, util, exceptions, bitcoin, blocks)
from lib import (send, order, btcpay, issuance, broadcast, bet, dividend, burn, api)

logging.basicConfig(filename='/tmp/counterparty.test.log')

# JSON‐RPC Options
CONFIGFILE = os.path.expanduser('~') + '/.bitcoin/bitcoin.conf'
RPCCONNECT = 'localhost'
# RPCPORT = '8332' # mainnet
RPCPORT = '18332' # testnet
try:
    with open(CONFIGFILE, 'r') as configfile:
        for line in configfile.readlines():
            if line.startswith('#'):
                continue
            array = line.replace('\n', '').split('=')
            if len(array) != 2:
                continue
            key, value = array[:2]
            if key == 'rpcuser': RPCUSER = value
            elif key == 'rpcpassword': RPCPASSWORD = value
            elif key == 'rpcconnect': RPCCONNECT = value
            elif key == 'rpcport': RPCCONNECT = value
except Exception:
    raise exceptions.BitcoinConfError('Put a (valid) copy of your \
bitcoin.conf in ~/.bitcoin/bitcoin.conf')
    sys.exit(1)
config.RPC = 'http://'+RPCUSER+':'+RPCPASSWORD+'@'+RPCCONNECT+':'+RPCPORT

config.DATABASE = '/tmp/counterparty.test.db'
try:
    os.remove(config.DATABASE)
except:
    pass
db = sqlite3.connect(config.DATABASE)
db.row_factory = sqlite3.Row
cursor = db.cursor()

tx_index = 0

config.BLOCK_FIRST = 0
config.BURN_START = 0
config.BURN_END = 9999999

source_default = 'mn6q3dS2EnDUx3bmyWc6D4szJNVGtaR7zc'
destination_default = 'n3BrDB6zDiEPWEE6wLxywFb4Yp9ZY5fHM7'
quantity = 100000000
small = int(quantity / 20)
expiration = 10
fee_required = 900000
fee_provided = 1000000

# Each tx has a block_index equal to its tx_index

def tx_insert (source, destination, btc_amount, fee, data):
    tx_hash = hashlib.sha256(chr(tx_index).encode('utf-8')).hexdigest()
    global tx_index
    cursor.execute('''INSERT INTO transactions(
                        tx_index,
                        tx_hash,
                        block_index,
                        block_time,
                        source,
                        destination,
                        btc_amount,
                        fee,
                        data) VALUES(?,?,?,?,?,?,?,?,?)''',
                        (tx_index,
                         tx_hash,
                         tx_index,
                         tx_index,
                         source,
                         destination,
                         btc_amount,
                         fee,
                         data)
                  )
    tx_index += 1

def get_tx_data (tx_hex):
    """Accepts unsigned transactions."""
    tx = bitcoin.rpc('decoderawtransaction', [tx_hex])['result']
    # Loop through outputs until you come upon OP_RETURN, then get the data.
    # NOTE: This assumes only one OP_RETURN output.
    data = None
    for vout in tx['vout']:
        asm = vout['scriptPubKey']['asm'].split(' ')
        if asm[0] == 'OP_RETURN' and len(asm) == 2:
            data = binascii.unhexlify(asm[1])

    # Destination is the first output with a valid address, (if it exists).
    destination, btc_amount = None, None
    for vout in tx['vout']:
        if 'addresses' in vout['scriptPubKey']:
            address = vout['scriptPubKey']['addresses'][0]
            if bitcoin.base58_decode(address, bitcoin.ADDRESSVERSION):  # If address is valid…
                destination, btc_amount = address, round(D(vout['value']) * config.UNIT)
                break
    return destination, btc_amount, data


def test_initialise():
    global db, cursor
    blocks.initialise(db, cursor)

def test_burn():
    global db, cursor
    unsigned_tx_hex = burn.create(source_default, quantity, test=True)
    assert unsigned_tx_hex == '0100000001c1d8c075936c3495f6d653c50f73d987f75448d97a750249b1eb83bee71b24ae0000000000ffffffff02de68f405000000001976a9144838d8b3588c4c7ba7c1d06f866e9b3739c6303788ac0000000000000000156a13544553540000003c50726f6f664f664275726e00000000'
    fee = quantity

    destination, btc_amount, data = get_tx_data(unsigned_tx_hex)
    tx_insert(source_default, destination, btc_amount, fee, data)
    cursor = blocks.parse_block(db, cursor, tx_index - 1)

def test_send():
    global db, cursor
    unsigned_tx_hex = send.create(source_default, destination_default, small, 1, test=True)
    assert unsigned_tx_hex == '0100000001c1d8c075936c3495f6d653c50f73d987f75448d97a750249b1eb83bee71b24ae0000000000ffffffff0336150000000000001976a914edb5c902eadd71e698a8ce05ba1d7b31efbaa57b88ac980dea0b000000001976a9144838d8b3588c4c7ba7c1d06f866e9b3739c6303788ac00000000000000001a6a185445535400000000000000000000000100000000004c4b4000000000'
    fee = config.MIN_FEE

    destination, btc_amount, data = get_tx_data(unsigned_tx_hex)
    tx_insert(source_default, destination, btc_amount, fee, data)
    cursor = blocks.parse_block(db, cursor, tx_index - 1)

def test_order_buy_xcp():
    global db, cursor
    unsigned_tx_hex = order.create(source_default, 0, small, 1, small * 2, expiration, 0, fee_provided, test=True)
    assert unsigned_tx_hex == '0100000001c1d8c075936c3495f6d653c50f73d987f75448d97a750249b1eb83bee71b24ae0000000000ffffffff029e07db0b000000001976a9144838d8b3588c4c7ba7c1d06f866e9b3739c6303788ac0000000000000000346a32544553540000000a000000000000000000000000004c4b4000000000000000010000000000989680000a000000000000000000000000'
    fee = config.MIN_FEE

    destination, btc_amount, data = get_tx_data(unsigned_tx_hex)
    tx_insert(source_default, destination, btc_amount, fee, data)
    cursor = blocks.parse_block(db, cursor, tx_index - 1)

def test_order_sell_xcp():
    global db, cursor
    unsigned_tx_hex = order.create(source_default, 1, int(small * 2.1), 0, small, expiration, fee_required, 0, test=True)
    print(unsigned_tx_hex)
    assert unsigned_tx_hex == '0100000001c1d8c075936c3495f6d653c50f73d987f75448d97a750249b1eb83bee71b24ae0000000000ffffffff02de49ea0b000000001976a9144838d8b3588c4c7ba7c1d06f866e9b3739c6303788ac0000000000000000346a32544553540000000a00000000000000010000000000a037a0000000000000000000000000004c4b40000a00000000000dbba000000000'
    fee = config.MIN_FEE

    destination, btc_amount, data = get_tx_data(unsigned_tx_hex)
    tx_insert(source_default, destination, btc_amount, fee, data)
    cursor = blocks.parse_block(db, cursor, tx_index - 1)

def test_btcpay():
    global db, cursor
    
    order_match_id = 'dbc1b4c900ffe48d575b5da5c638040125f65db0fe3e24494b76ea986457d986084fed08b978af4d7d196a7446a86b58009e636b611db16211b65a9aadff29c5'
    unsigned_tx_hex = btcpay.create(order_match_id, test=True)
    logging.error(unsigned_tx_hex)
    assert unsigned_tx_hex == '0100000001c1d8c075936c3495f6d653c50f73d987f75448d97a750249b1eb83bee71b24ae0000000000ffffffff03404b4c00000000001976a9144838d8b3588c4c7ba7c1d06f866e9b3739c6303788ac8ed79d0b000000001976a9144838d8b3588c4c7ba7c1d06f866e9b3739c6303788ac00000000000000004a6a48544553540000000bdbc1b4c900ffe48d575b5da5c638040125f65db0fe3e24494b76ea986457d986084fed08b978af4d7d196a7446a86b58009e636b611db16211b65a9aadff29c500000000'
    fee = config.MIN_FEE

    destination, btc_amount, data = get_tx_data(unsigned_tx_hex)
    tx_insert(source_default, destination, btc_amount, fee, data)
    cursor = blocks.parse_block(db, cursor, tx_index - 1)

def test_parse_from_the_start():
    global db, cursor
    blocks.initialise(db, cursor)
    for i in range(tx_index):
        cursor = blocks.parse_block(db, cursor, i)

"""
def test_db_dump():
    import difflib
    data = '\n'.join(db.iterdump())
    with open('test/db.test.dump', 'w') as f:
        print(difflib.SequenceMatcher(None, data, f.read())
"""



# Can’t do follow().

"""
lib/send.py:30:def parse (db, cursor, tx, message):
lib/api.py:8:def get_balances (address=None, asset_id=None):
lib/api.py:23:def get_sends (validity=None, source=None, destination=None):
lib/api.py:38:def get_orders (validity=None, address=None, show_empty=True, show_expired=True):
lib/api.py:61:def get_order_matches (validity=None, addresses=[], show_expired=True):
lib/api.py:85:def get_btcpays (validity=None):
lib/api.py:98:def get_issuances (validity=None, asset_id=None, issuer=None):
lib/api.py:114:def get_broadcasts (validity=None, source=None):
lib/api.py:129:def get_bets (validity=None, address=None, show_empty=True, show_expired=True):
lib/api.py:147:def get_bet_matches (validity=None, addresses=None, show_expired=True):
lib/api.py:166:def get_dividends (validity=None, address=None, asset_id=None):
lib/api.py:181:def get_burns (validity=True, address=None):
lib/api.py:196:def get_history (address):

lib/util.py:18:def short (string):
lib/util.py:24:def isodt (epoch_time):

lib/util.py:27:def get_time_left (unmatched):
lib/util.py:32:def get_order_match_time_left (matched):
lib/util.py:40:def get_asset_id (asset):
lib/util.py:44:def get_asset_name (asset_id):

lib/util.py:49:def debit (db, cursor, address, asset_id, amount):
lib/util.py:69:def credit (db, cursor, address, asset_id, amount):

lib/util.py:88:def good_feed (cursor, feed_address):
lib/util.py:103:def devise (quantity, asset_id, precision=8):
lib/dividend.py:15:def create (source, amount_per_share, asset_id):
lib/dividend.py:36:def parse (db, cursor, tx, message):
lib/bet.py:26:def get_fee_multiplier (feed_address):
lib/bet.py:39:def create (source, feed_address, bet_type, deadline, wager_amount,
lib/bet.py:67:def parse (db, cursor, tx, message):
lib/bet.py:141:def bet_match (db, cursor, tx):
lib/bet.py:249:def expire (db, cursor, block_index):
lib/issuance.py:13:def create (source, asset_id, amount, divisible):
lib/issuance.py:31:def parse (db, cursor, tx, message):
lib/burn.py:39:def parse (db, cursor, tx, message):
lib/broadcast.py:36:def create (source, timestamp, value, fee_multiplier, text):
lib/broadcast.py:43:def parse (db, cursor, tx, message):
lib/btcpay.py:14:def create (order_match_id):
lib/btcpay.py:42:def parse (db, cursor, tx, message):
lib/order.py:30:def parse (db, cursor, tx, message):
lib/order.py:104:def order_match (db, cursor, tx):
lib/order.py:202:def expire (db, cursor, block_index):
lib/blocks.py:19:def parse_block (db, cursor, block_index):
lib/blocks.py:71:def initialise(db, cursor):
lib/blocks.py:277:def get_tx_info (tx):
lib/blocks.py:318:def follow ():
lib/bitcoin.py:28:def rpc (method, params):
lib/bitcoin.py:44:def bitcoind_check ():
lib/bitcoin.py:52:def base58_decode (s, version):
lib/bitcoin.py:117:def serialize (inputs, outputs, data):
lib/bitcoin.py:161:def get_inputs (source, amount, fee):
lib/bitcoin.py:173:def transaction (source, destination, btc_amount, fee, data, ask=False):
"""
