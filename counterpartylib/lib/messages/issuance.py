#! /usr/bin/python3

"""
Allow simultaneous lock and transfer.
"""

import struct
import decimal
import json
import logging
logger = logging.getLogger(__name__)
D = decimal.Decimal

from counterpartylib.lib import (assetgroup, config, util, exceptions, util, message_type)
from counterpartylib.lib.messages import (dispenser)

FORMAT_1 = '>QQ?'
LENGTH_1 = 8 + 8 + 1
FORMAT_2 = '>QQB?If'
LENGTH_2 = 8 + 8 + 1 + 1 + 4 + 4
SUBASSET_FORMAT = '>QQBB'
SUBASSET_FORMAT_LENGTH = 8 + 8 + 1 + 1
ID = 20
SUBASSET_ID = 21
# NOTE: Pascal strings are used for storing descriptions for backwards‐compatibility.

def initialise(db):
    cursor = db.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS issuances(
                      tx_index INTEGER PRIMARY KEY,
                      tx_hash TEXT UNIQUE,
                      block_index INTEGER,
                      asset TEXT,
                      quantity INTEGER,
                      divisible BOOL,
                      source TEXT,
                      issuer TEXT,
                      transfer BOOL,
                      callable BOOL,
                      call_date INTEGER,
                      call_price REAL,
                      description TEXT,
                      fee_paid INTEGER,
                      locked BOOL,
                      status TEXT,
                      asset_longname TEXT,
                      listed BOOL,
                      reassignable BOOL,
                      vendable BOOL,
                      fungible BOOL,
                      FOREIGN KEY (tx_index, tx_hash, block_index) REFERENCES transactions(tx_index, tx_hash, block_index))
                   ''')

    # Add asset_longname for sub-assets
    #   SQLite can’t do `ALTER TABLE IF COLUMN NOT EXISTS`.
    columns = [column['name'] for column in cursor.execute('''PRAGMA table_info(issuances)''')]
    if 'asset_longname' not in columns:
        cursor.execute('''ALTER TABLE issuances ADD COLUMN asset_longname TEXT''')

    if 'listed' not in columns:
        cursor.execute('''ALTER TABLE issuances ADD COLUMN listed BOOL''')

    if 'reassignable' not in columns:
        cursor.execute('''ALTER TABLE issuances ADD COLUMN reassignable BOOL''')

    if 'vendable' not in columns:
        cursor.execute('''ALTER TABLE issuances ADD COLUMN vendable BOOL''')

    if 'fungible' not in columns:
        cursor.execute('''ALTER TABLE issuances ADD COLUMN fungible BOOL''')

    # If sweep_hotifx activated, Create issuances copy, copy old data, drop old table, rename new table, recreate indexes
    #   SQLite can’t do `ALTER TABLE IF COLUMN NOT EXISTS` nor can drop UNIQUE constraints
    if 'msg_index' not in columns:
            cursor.execute('''CREATE TABLE IF NOT EXISTS new_issuances(
                              tx_index INTEGER,
                              tx_hash TEXT,
                              msg_index INTEGER DEFAULT 0,
                              block_index INTEGER,
                              asset TEXT,
                              quantity INTEGER,
                              divisible BOOL,
                              source TEXT,
                              issuer TEXT,
                              transfer BOOL,
                              callable BOOL,
                              call_date INTEGER,
                              call_price REAL,
                              description TEXT,
                              fee_paid INTEGER,
                              locked BOOL,
                              status TEXT,
                              asset_longname TEXT,
                              listed BOOL,
                              reassignable BOOL,
                              vendable BOOL,
                              fungible BOOL,
                              PRIMARY KEY (tx_index, msg_index),
                              FOREIGN KEY (tx_index, tx_hash, block_index) REFERENCES transactions(tx_index, tx_hash, block_index),
                              UNIQUE (tx_hash, msg_index))
                           ''')
            cursor.execute('''INSERT INTO new_issuances(tx_index, tx_hash, msg_index,
                block_index, asset, quantity, divisible, source, issuer, transfer, callable,
                call_date, call_price, description, fee_paid, locked, status, asset_longname, listed, reassignable, vendable, fungible)
                SELECT tx_index, tx_hash, 0, block_index, asset, quantity, divisible, source,
                issuer, transfer, callable, call_date, call_price, description, fee_paid,
                locked, status, asset_longname, listed, reassignable, vendable, fungible FROM issuances''', {})
            cursor.execute('DROP TABLE issuances')
            cursor.execute('ALTER TABLE new_issuances RENAME TO issuances')

    cursor.execute('''CREATE INDEX IF NOT EXISTS
                      block_index_idx ON issuances (block_index)
                    ''')
    cursor.execute('''CREATE INDEX IF NOT EXISTS
                      valid_asset_idx ON issuances (asset, status)
                   ''')
    cursor.execute('''CREATE INDEX IF NOT EXISTS
                      status_idx ON issuances (status)
                   ''')
    cursor.execute('''CREATE INDEX IF NOT EXISTS
                      source_idx ON issuances (source)
                   ''')

    cursor.execute('''CREATE INDEX IF NOT EXISTS
                      asset_longname_idx ON issuances (asset_longname)
                   ''')

    assetgroup.initialise(db)

def validate (db, source, destination, asset, quantity, divisible, listed, reassignable, vendable, fungible, callable_, call_date, call_price, description, subasset_parent, subasset_longname, block_index):
    problems = []
    fee = 0

    if asset in (config.BTC, config.XCP):
        problems.append('cannot issue {} or {}'.format(config.BTC, config.XCP))

    if call_date is None: call_date = 0
    if call_price is None: call_price = 0.0
    if description is None: description = ""
    if divisible is None: divisible = True
    if listed is None: listed = True
    if reassignable is None: reassignable = True
    if vendable is None: vendable = True
    if fungible is None: fungible = True

    if isinstance(call_price, int): call_price = float(call_price)
    #^ helps especially with calls from JS‐based clients, where parseFloat(15) returns 15 (not 15.0), which json takes as an int

    if not isinstance(quantity, int):
        problems.append('quantity must be in satoshis')
        return call_date, call_price, problems, fee, description, divisible, listed, reassignable, vendable, fungible, None, None
    if call_date and not isinstance(call_date, int):
        problems.append('call_date must be epoch integer')
        return call_date, call_price, problems, fee, description, divisible, listed, reassignable, vendable, fungible, None, None
    if call_price and not isinstance(call_price, float):
        problems.append('call_price must be a float')
        return call_date, call_price, problems, fee, description, divisible, listed, reassignable, vendable, fungible, None, None

    if util.enabled('non_fungible_assets'):
        if not fungible:
            if divisible:
                problems.append('Cannot create the asset with non-fungible and divisible')
            elif quantity != 1:
                problems.append('non-fungible asset can issue only 1 asset')
    elif not fungible:
        problems.append('non-fungible assets not enabled')

    if quantity < 0: problems.append('negative quantity')
    if call_price < 0: problems.append('negative call price')
    if call_date < 0: problems.append('negative call date')

    # Callable, or not.
    if not callable_:
        if block_index >= 312500 or config.TESTNET or config.REGTEST: # Protocol change.
            call_date = 0
            call_price = 0.0
        elif block_index >= 310000:                 # Protocol change.
            if call_date:
                problems.append('call date for non‐callable asset')
            if call_price:
                problems.append('call price for non‐callable asset')

    # Valid re-issuance?
    cursor = db.cursor()
    cursor.execute('''SELECT * FROM issuances
                      WHERE (status = ? AND asset = ?)
                      ORDER BY tx_index ASC''', ('valid', asset))
    issuances = cursor.fetchall()
    cursor.close()
    reissued_asset_longname = None
    if issuances:
        reissuance = True
        last_issuance = issuances[-1]
        reissued_asset_longname = last_issuance['asset_longname']
        issuance_locked = False
        if util.enabled('issuance_lock_fix'):
            for issuance in issuances:
                if issuance['locked']:
                    issuance_locked = True
                    break
        elif last_issuance['locked']:
            # before the issuance_lock_fix, only the last issuance was checked
            issuance_locked = True

        if last_issuance['issuer'] != source:
            problems.append('issued by another address')
        if bool(last_issuance['divisible']) != bool(divisible):
            problems.append('cannot change divisibility')
        if bool(last_issuance['listed']) != bool(listed):
            problems.append('cannot change listing flag')
        if bool(last_issuance['reassignable']) != bool(reassignable):
            problems.append('cannot change reassignable flag')
        if bool(last_issuance['vendable']) != bool(vendable):
            if (last_issuance['vendable'] == False # Don't cast to bool.
                or util.enabled('enable_vendable_fix')):
                problems.append('Cannot change vendable flag')
            elif dispenser.is_opened(db, asset):
                problems.append('Cannot change vendable flag because the asset is dispending')
        if bool(last_issuance['callable']) != bool(callable_):
            problems.append('cannot change callability')
        if last_issuance['call_date'] > call_date and (call_date != 0 or (block_index < 312500 and (not config.TESTNET or not config.REGTEST))):
            problems.append('cannot advance call date')
        if last_issuance['call_price'] > call_price:
            problems.append('cannot reduce call price')
        if issuance_locked and quantity:
            problems.append('locked asset and non‐zero quantity')
    else:
        reissuance = False
        if description.lower() == 'lock' and fungible:
            problems.append('cannot lock a non‐existent asset')
        if destination:
            problems.append('cannot transfer a non‐existent asset')

    # validate parent ownership for subasset and asset group
    if subasset_longname is not None:
        cursor = db.cursor()
        if fungible:
            cursor.execute('''SELECT * FROM issuances
                              WHERE (status = ? AND asset = ?)
                              ORDER BY tx_index ASC''', ('valid', subasset_parent))
            parent_issuances = cursor.fetchall()
            if parent_issuances:
                last_parent_issuance = parent_issuances[-1]
                if last_parent_issuance['issuer'] != source:
                    problems.append('parent asset owned by another address')
            else:
                problems.append('parent asset not found')
        else:
            problems += assetgroup.validate(db, subasset_longname, source)

        cursor.close()

    if subasset_longname is not None and not reissuance:
        if fungible:
            # validate subasset issuance is not a duplicate
            cursor = db.cursor()
            cursor.execute('''SELECT * FROM assets
                              WHERE (asset_longname = ?)''', (subasset_longname,))
            assets = cursor.fetchall()
            if len(assets) > 0:
                problems.append('subasset already exists')
        else:
            pass # no need to check if the asset is non-fungible.

        # validate that the actual asset is numeric
        if asset[0] != 'A':
            problems.append('parent asset must be a numeric asset')


    # Check for existence of fee funds.
    if quantity or (block_index >= 315000 or config.TESTNET or config.REGTEST):   # Protocol change.
        if not reissuance or (block_index < 310000 and not config.TESTNET and not config.REGTEST):  # Pay fee only upon first issuance. (Protocol change.)
            cursor = db.cursor()
            cursor.execute('''SELECT * FROM balances
                              WHERE (address = ? AND asset = ?)''', (source, config.XCP))
            balances = cursor.fetchall()
            cursor.close()
            if util.enabled('numeric_asset_names'):  # Protocol change.
                if subasset_longname is not None:
                    if util.enabled('subassets') and fungible: # Protocol change.
                        # subasset issuance is 0.25
                        fee = int(0.25 * config.UNIT)
                    elif util.enabled('non_fungible_assets') and not fungible:
                        fee = int(0.0025 * config.UNIT)
                    else:
                        fee = int(0.0025 * config.UNIT) # same as non-fungible but it will be invalidated.
                elif len(asset) >= 13:
                    fee = 0
                else:
                    fee = int(0.5 * config.UNIT)
                if util.enabled('fee_revision_2021_1q'):
                    fee *= 100
            elif block_index >= 291700 or config.TESTNET or config.REGTEST:     # Protocol change.
                fee = int(0.5 * config.UNIT)
            elif block_index >= 286000 or config.TESTNET or config.REGTEST:   # Protocol change.
                fee = 5 * config.UNIT
            elif block_index > 281236 or config.TESTNET or config.REGTEST:    # Protocol change.
                fee = 5
            if fee and (not balances or balances[0]['quantity'] < fee):
                problems.append('insufficient funds')

    if not (block_index >= 317500 or config.TESTNET or config.REGTEST):  # Protocol change.
        if len(description) > 42:
            problems.append('description too long')

    if not listed and not util.enabled('delisted_assets', block_index=block_index):
        problems.append('invalid: delisted assets not supported yet.')

    if not reassignable and not util.enabled('non_reassignable_assets', block_index=block_index):
        problems.append('invalid: non-reassignable assets not supported yet.')

    # For SQLite3
    call_date = min(call_date, config.MAX_INT)
    total = sum([issuance['quantity'] for issuance in issuances])
    assert isinstance(quantity, int)
    if total + quantity > config.MAX_INT:
        problems.append('total quantity overflow')

    if destination and quantity:
        problems.append('cannot issue and transfer simultaneously')

    # For SQLite3
    if util.enabled('integer_overflow_fix', block_index=block_index) and (fee > config.MAX_INT or quantity > config.MAX_INT):
        problems.append('integer overflow')

    return call_date, call_price, problems, fee, description, divisible, listed, reassignable, vendable, fungible, reissuance, reissued_asset_longname


def compose (db, source, transfer_destination, asset, quantity, divisible, listed, reassignable, vendable, fungible, description):

    # Callability is deprecated, so for re‐issuances set relevant parameters
    # to old values; for first issuances, make uncallable.
    cursor = db.cursor()
    cursor.execute('''SELECT * FROM issuances
                      WHERE (status = ? AND asset = ?)
                      ORDER BY tx_index ASC''', ('valid', asset))
    issuances = cursor.fetchall()
    if issuances:
        last_issuance = issuances[-1]
        callable_ = last_issuance['callable']
        call_date = last_issuance['call_date']
        call_price = last_issuance['call_price']
    else:
        callable_ = False
        call_date = 0
        call_price = 0.0
    cursor.close()

    # check subasset
    subasset_parent = None
    subasset_longname = None
    # `funginble` can be `None` here. So it must be checked if not `False`.
    if util.enabled('subassets') and fungible is not False: # Protocol change.
        subasset_parent, subasset_longname = util.parse_subasset_from_asset_name(asset)
        if subasset_longname is not None:
            # try to find an existing subasset
            sa_cursor = db.cursor()
            sa_cursor.execute('''SELECT * FROM assets
                              WHERE (asset_longname = ?)''', (subasset_longname,))
            assets = sa_cursor.fetchall()
            sa_cursor.close()
            if len(assets) > 0:
                # this is a reissuance
                asset = assets[0]['asset_name']
            else:
                # this is a new issuance
                #   generate a random numeric asset id which will map to this subasset
                asset = util.generate_random_asset()
    elif util.enabled('non_fungible_assets') and fungible is False:
        # non-fungible is always a new issuance.
        subasset_parent = util.generate_random_asset()
        subasset_longname = asset
        asset = subasset_parent

    call_date, call_price, problems, fee, description, divisible, listed, reassignable, vendable, fungible, reissuance, _ = validate(db, source, transfer_destination, asset, quantity, divisible, listed, reassignable, vendable, fungible, callable_, call_date, call_price, description, subasset_parent, subasset_longname, util.CURRENT_BLOCK_INDEX)
    if problems: raise exceptions.ComposeError(problems)

    asset_id = util.generate_asset_id(asset, util.CURRENT_BLOCK_INDEX)
    encoded_description = description.encode('utf-8')
    if subasset_longname is None or reissuance:
        # Type 20 standard issuance FORMAT_2 >QQB?If
        #   used for standard issuances and all reissuances
        data = message_type.pack(ID)
        if len(encoded_description) <= 42:
            curr_format = FORMAT_2 + '{}p'.format(len(encoded_description) + 1)
        else:
            curr_format = FORMAT_2 + '{}s'.format(len(encoded_description))
        data += struct.pack(curr_format, asset_id, quantity,
            (1 if divisible else 0)
            | (0 if listed else 2)
            | (0 if reassignable else 4)
            | (0 if vendable else 8)
            | (0 if fungible else 16),
            1 if callable_ else 0, call_date or 0, call_price or 0.0, encoded_description)
    else:
        # Type 21 subasset issuance SUBASSET_FORMAT >QQ?B
        #   Used both of "initial subasset issuance" and "non-fungible asset issuance"
        # compacts a subasset name to save space
        compacted_subasset_longname = util.compact_subasset_longname(subasset_longname)
        compacted_subasset_length = len(compacted_subasset_longname)
        data = message_type.pack(SUBASSET_ID)
        curr_format = SUBASSET_FORMAT + '{}s'.format(compacted_subasset_length) + '{}s'.format(len(encoded_description))
        data += struct.pack(curr_format, asset_id, quantity,
            (1 if divisible else 0)
            | (0 if listed else 2)
            | (0 if reassignable else 4)
            | (0 if vendable else 8)
            | (0 if fungible else 16),
            compacted_subasset_length, compacted_subasset_longname, encoded_description)

    if transfer_destination:
        destination_outputs = [(transfer_destination, None)]
    else:
        destination_outputs = []
    return (source, destination_outputs, data)

def parse (db, tx, message, message_type_id):
    issuance_parse_cursor = db.cursor()

    # Unpack message.
    try:
        subasset_longname = None
        if message_type_id == SUBASSET_ID:
            if not util.enabled('subassets', block_index=tx['block_index']):
                logger.warn("subassets are not enabled at block %s" % tx['block_index'])
                raise exceptions.UnpackError

            # parse a subasset original issuance message
            asset_id, quantity, flags, compacted_subasset_length = struct.unpack(SUBASSET_FORMAT, message[0:SUBASSET_FORMAT_LENGTH])
            divisible = ((flags & 1) != 0)
            listed = ((flags & 2) == 0)
            reassignable = ((flags & 4) == 0)
            vendable = ((flags & 8) == 0)
            fungible = ((flags & 16) == 0)
            description_length = len(message) - SUBASSET_FORMAT_LENGTH - compacted_subasset_length
            if description_length < 0:
                logger.warn("invalid subasset length: [issuance] tx [%s]: %s" % (tx['tx_hash'], compacted_subasset_length))
                raise exceptions.UnpackError
            messages_format = '>{}s{}s'.format(compacted_subasset_length, description_length)
            compacted_subasset_longname, description = struct.unpack(messages_format, message[SUBASSET_FORMAT_LENGTH:])
            subasset_longname = util.expand_subasset_longname(compacted_subasset_longname)
            callable_, call_date, call_price = False, 0, 0.0
            try:
                description = description.decode('utf-8')
            except UnicodeDecodeError:
                description = description.decode('utf-8', 'replace') if util.enabled('utf-8_codec_fixes') else ''
        elif (tx['block_index'] > 283271 or config.TESTNET or config.REGTEST) and len(message) >= LENGTH_2: # Protocol change.
            if len(message) - LENGTH_2 <= 42:
                curr_format = FORMAT_2 + '{}p'.format(len(message) - LENGTH_2)
            else:
                curr_format = FORMAT_2 + '{}s'.format(len(message) - LENGTH_2)
            asset_id, quantity, flags, callable_, call_date, call_price, description = struct.unpack(curr_format, message)
            divisible = ((flags & 1) != 0)
            listed = ((flags & 2) == 0)
            reassignable = ((flags & 4) == 0)
            vendable = ((flags & 8) == 0)
            fungible = ((flags & 16) == 0)
            call_price = round(call_price, 6) # TODO: arbitrary
            try:
                description = description.decode('utf-8')
            except UnicodeDecodeError:
                description = description.decode('utf-8', 'replace') if util.enabled('utf-8_codec_fixes') else ''
        else:
            if len(message) != LENGTH_1:
                raise exceptions.UnpackError
            asset_id, quantity, flags = struct.unpack(FORMAT_1, message)
            divisible = ((flags & 1) != 0)
            listed = ((flags & 2) == 0)
            reassignable = ((flags & 4) == 0)
            vendable = ((flags & 8) == 0)
            fungible = ((flags & 16) == 0)
            callable_, call_date, call_price, description = False, 0, 0.0, ''
        try:
            asset = util.generate_asset_name(asset_id, tx['block_index'])
            status = 'valid'
        except exceptions.AssetIDError:
            asset = None
            status = 'invalid: bad asset name'
    except exceptions.UnpackError as e:
        asset, quantity, divisible, listed, reassignable, vendable, fungible, callable_, call_date, call_price, description = None, None, None, None, None, None, None, None, None, None, None
        status = 'invalid: could not unpack'

    # parse and validate the subasset from the message
    subasset_parent = None
    if status == 'valid' and subasset_longname is not None: # Protocol change.
        if fungible:
            try:
                # ensure the subasset_longname is valid
                util.validate_subasset_longname(subasset_longname)
                subasset_parent, subasset_longname = util.parse_subasset_from_asset_name(subasset_longname)
            except exceptions.AssetNameError as e:
                asset = None
                status = 'invalid: bad subasset name'
        else:
            subasset_parent = asset
            try:
                util.validate_subasset_longname(subasset_longname, subasset_longname)
            except exceptions.AssetNameError:
                asset = None
                status = 'invalid: bad assetgroup name'

    reissuance = None
    fee = 0
    if status == 'valid':
        call_date, call_price, problems, fee, description, divisible, listed, reassignable, vendable, fungible, reissuance, reissued_asset_longname = validate(db, tx['source'], tx['destination'], asset, quantity, divisible, listed, reassignable, vendable, fungible, callable_, call_date, call_price, description, subasset_parent, subasset_longname, block_index=tx['block_index'])

        if problems: status = 'invalid: ' + '; '.join(problems)
        if not util.enabled('integer_overflow_fix', block_index=tx['block_index']) and 'total quantity overflow' in problems:
            quantity = 0

    if tx['destination']:
        issuer = tx['destination']
        transfer = True
        quantity = 0
    else:
        issuer = tx['source']
        transfer = False

    # Debit fee.
    if status == 'valid':
        util.debit(db, tx['source'], config.XCP, fee, action="issuance fee", event=tx['tx_hash'])

    # Lock?
    lock = False
    if status == 'valid':
        if not reissuance:
            # Add to table of assets.
            bindings= {
                'asset_id': str(asset_id),
                'asset_name': str(asset),
                'block_index': tx['block_index'],
                'asset_longname': subasset_longname if fungible else None,
                'asset_group': None if fungible else subasset_longname
            }
            sql='insert into assets values(:asset_id, :asset_name, :block_index, :asset_longname, :asset_group)'
            issuance_parse_cursor.execute(sql, bindings)

            if not fungible:
                lock = True

            assert not (description and description.lower() == 'lock') # Should rejected in validate().

        elif description and description.lower() == 'lock': # reissuance. fungible.
            lock = True
            cursor = db.cursor()
            issuances = list(cursor.execute('''SELECT * FROM issuances
                                               WHERE (status = ? AND asset = ?)
                                               ORDER BY tx_index ASC''', ('valid', asset)))
            cursor.close()
            description = issuances[-1]['description']  # Use last description. (Assume previous issuance exists because tx is valid.)
            timestamp, value_int, fee_fraction_int = None, None, None

    if status == 'valid' and reissuance:
        # when reissuing, add the asset_longname to the issuances table for API lookups
        asset_longname = reissued_asset_longname
    else:
        asset_longname = subasset_longname

    # Add parsed transaction to message-type–specific table.
    bindings= {
        'tx_index': tx['tx_index'],
        'tx_hash': tx['tx_hash'],
        'block_index': tx['block_index'],
        'asset': asset,
        'quantity': quantity,
        'divisible': divisible,
        'vendable': vendable,
        'listed': listed,
        'reassignable': reassignable,
        'fungible': fungible,
        'source': tx['source'],
        'issuer': issuer,
        'transfer': transfer,
        'callable': callable_,
        'call_date': call_date,
        'call_price': call_price,
        'description': description,
        'fee_paid': fee,
        'locked': lock,
        'status': status,
        'asset_longname': asset_longname,
    }
    if "integer overflow" not in status:
        sql='insert into issuances values(:tx_index, :tx_hash, 0, :block_index, :asset, :quantity, :divisible, :source, :issuer, :transfer, :callable, :call_date, :call_price, :description, :fee_paid, :locked, :status, :asset_longname, :listed, :reassignable, :vendable, :fungible)'
        issuance_parse_cursor.execute(sql, bindings)
    else:
        logger.warn("Not storing [issuance] tx [%s]: %s" % (tx['tx_hash'], status))
        logger.debug("Bindings: %s" % (json.dumps(bindings), ))

    if not fungible:
        assetgroup.create(db,
            tx['tx_index'],
            tx['tx_hash'],
            tx['block_index'],
            asset_longname,
            issuer,
            status)

    # Credit.
    if status == 'valid' and quantity:
        util.credit(db, tx['source'], asset, quantity, action="issuance", event=tx['tx_hash'])


    issuance_parse_cursor.close()

def is_vendable(db, asset):
    if asset == config.XCP:
        return True # Always vendable.

    asset = util.resolve_subasset_longname(db, asset)
    cursor = db.cursor()
    issuances = list(cursor.execute('''SELECT vendable, reassignable, listed FROM issuances
                                               WHERE (status = ? AND asset = ?)
                                               ORDER BY tx_index DESC LIMIT 1''', ('valid', asset)))
    cursor.close()
    if (len(issuances) <= 0):
        return False;

    vendable = issuances[0]['vendable']  # Use the last issuance.
    reassignable = issuances[0]['reassignable']
    listed = issuances[0]['listed']

    if not util.enabled('dispensers'):
        return False
    elif not util.enabled('enable_vendable_fix') and (reassignable == False or listed == False):
        return False
    else:
        return vendable

def find_issuance_by_tx_hash(db, tx_hash):
    cursor = db.cursor()
    issuances = list(cursor.execute('''SELECT asset FROM issuances WHERE tx_hash = ?''',
        (tx_hash,)))
    return issuances[0]['asset'] if len(issuances) != 0 else None

# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
