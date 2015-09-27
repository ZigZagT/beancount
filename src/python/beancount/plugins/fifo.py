"""A plugin that automatically converts postings at price to postings held at
cost, applying an automatic booking algorithm in assigning the cost bases and
matching lots.

This plugin restricts itself to applying these transformations within a
particular account, which you provide. For each of those accounts, it also
requires a corresponding Income account to book the profit/loss of reducing
lots (i.e., sales):

  plugin "beancount.plugins.fifo" "Assets:Bitcoin,Income:Bitcoin"

Then, simply input the transactions with price conversion. We use "Bitcoins" in
this example, converting Bitcoin purchases that were carried out as currency
into maintaining these with cost basis, for tax reporting purposes:

  2015-09-04 * "Buy some bitcoins"
    Assets:Bank          -1000.00 USD
    Assets:Bitcoin       4.333507 BTC @ 230.76 USD

  2015-09-05 * "Buy some more bitcoins at a different price"
    Assets:Bank          -1000.00 USD
    Assets:Bitcoin       4.345747 BTC @ 230.11 USD

  2015-09-20 * "Use (sell) some bitcoins"
    Assets:Bitcoin       -6.000000 BTC @ 230.50 USD
    Expenses:Something

The result is that cost bases are inserted on augmenting lots:

  2015-09-04 * "Buy some bitcoins"
    Assets:Bitcoin  4.333507 BTC {230.76 USD} @ 230.76 USD
    Assets:Bank     -1000.00 USD

  2015-09-05 * "Buy some more bitcoins at a different price"
    Assets:Bitcoin  4.345747 BTC {230.11 USD} @ 230.11 USD
    Assets:Bank     -1000.00 USD

While on reducing lots, matching FIFO lots are automatically found and the
corresponding cost basis added:

  2015-09-20 * "Use (sell) some bitcoins"
    Assets:Bitcoin          -4.333507 BTC {230.76 USD} @ 230.50 USD
    Assets:Bitcoin          -1.666493 BTC {230.11 USD} @ 230.50 USD
    Income:Bitcoin         0.47677955 USD
    Expenses:Something  1383.00000000 USD

Note that multiple lots were required to fulfill the sale quantity here. As in
this example, this may result in multiple lots being created for a single one.

Finally, Beancount will eventually support booking methods built-in, but this is
a quick method that shows how to hack your own booking method via
transformations of the postings that run in in a plugin.

Implementation notes:

- Instead of keeping a list of (Position, Transcation) pairs for the pending
  FIFO lots, we really ought to use a beancount.core.inventory.Inventory
  instance. However, the class does not contain sufficient data to carry out
  FIFO booking at the moment. A newer implementation, living in the "booking"
  branch, does, and will be used in the future.

- This code assumes that a positive number of units is an augmenting lot and a
  reducing one has a negative number of units. This is not strictly true;
  however, we would need an Inventory in order to figrue this out. This will be
  done in the future and is not difficult to do.

"""

__author__ = 'Martin Blais <blais@furius.ca>'
__plugins__ = ('book_price_conversions_plugin',)

import argparse
import collections
import copy
import logging
import pprint
import re
import sys
import uuid

from beancount.core.number import ZERO
from beancount.core.number import D
from beancount.core.amount import Amount
from beancount.core.position import Position
from beancount.core.inventory import Inventory
from beancount.core import data
from beancount.parser import printer
from beancount import loader
from beancount.reports import table


# The name of the metadata field used to link matched postings.
META = 'trades'


# An error in the configuration for this plugin.
ConfigError = collections.namedtuple('ConfigError', 'source message entry')

# A failure to find a matching lot.
FIFOError = collections.namedtuple('FIFOError', 'source message entry')


def is_matching(posting, account):
    """"A special posting is one in the FIFO currency that doesn't have a cost and that
    has a price.

    Args:
      posting: An instance of a Posting.
      account: The account name configured.
    Returns:
      A boolean, true if this posting is one that we should be concerned with.
    """
    return (posting.account == account and
            posting.position.lot.cost is None and
            posting.price is not None)


def augment_inventory(fifo_lots, posting, eindex):
    """Add the lots from the given posting to the running inventory.

    Args:
      fifo_lots: A list of pending ([number], Posting, Transaction) to be matched.
        The number is modified in-place, destructively.
      posting: The posting whose position is to be added.
      eindex: The index of the parent transaction housing this posting.
    Returns:
      A new posting with cost basis inserted to be added to a transformed transaction.
    """
    lot = posting.position.lot._replace(cost=copy.copy(posting.price))
    number = posting.position.number
    pos = Position(lot, number)
    new_posting = posting._replace(position=pos)
    fifo_lots.append( ([number], new_posting, eindex) )
    return new_posting


def reduce_inventory(fifo_lots, posting, eindex):
    """Match a reducing posting against a list of lots.

    Args:
      fifo_lots: A list of pending ([number], Posting, Transaction) to be matched.
        The number is modified in-place, destructively.
      posting: The posting whose position is to be added.
      eindex: The index of the parent transaction housing this posting.
    Returns:
      A tuple of
        postings: A list of new Posting instances corresponding to the given
          posting, that were booked to the current list of lots.
        matches: A list of pairs of (augmenting-posting, reducing-posting).
        pnl: A Decimal, the P/L incurred in reducing these lots.
        errors: A list of new errors generated in reducing these lots.
    """
    new_postings = []
    matches = []
    pnl = ZERO
    errors = []

    match_number = -posting.position.number
    match_currency = posting.position.lot.currency
    cost_currency = posting.price.currency
    while match_number != ZERO:

        # Find the first lot with matching currency.
        for fnumber, fposting, findex in fifo_lots:
            fposition = fposting.position
            if (fposition.lot.currency == match_currency and
                fposition.lot.cost and fposition.lot.cost.currency == cost_currency):
                assert fnumber[0] > ZERO, "Internal error, zero lot"
                break
        else:
            errors.append(
                FIFOError(posting.meta,
                          "Could not match position {}".format(posting), None))
            break

        # Reduce the FIFO lots.
        number = min(match_number, fnumber[0])
        cost = fposition.lot.cost
        match_number -= number
        fnumber[0] -= number
        if fnumber[0] == ZERO:
            fifo_lots.pop(0)

        # Add a corresponding posting.
        pos = Position(posting.position.lot._replace(cost=copy.copy(cost)),
                       -number)
        rposting = posting._replace(position=pos)
        new_postings.append(rposting)

        # Update the P/L.
        pnl += number * (posting.price.number - pos.lot.cost.number)

        # Add to the list of matches.
        matches.append(((findex, fposting),
                        (eindex, rposting)))

    return new_postings, matches, pnl, errors


def link_entries_with_metadata(entries, all_matches):
    """Modify the entries in-place to add matching links to postings.

    Args:
      entries: The list of entries to modify.
    Returns:
      A list of pairs of (index, Posting) for the new (augmenting, reducing)
      annotated postings.
    """
    new_matches = []

    # Allocate trade names and compute a map of posting to trade names.
    link_map = collections.defaultdict(list)
    for (aug_index, aug_posting), (red_index, red_posting) in all_matches:
        link = 'fifo-{}'.format(str(uuid.uuid4()).split('-')[-1])
        link_map[id(aug_posting)].append(link)
        link_map[id(red_posting)].append(link)

    # Modify the postings.
    postings_repl_map = {}
    for entry in entries:
        if isinstance(entry, data.Transaction):
            for index, posting in enumerate(entry.postings):
                links = link_map.pop(id(posting), None)
                if links:
                    new_posting = posting._replace(meta=posting.meta.copy())
                    new_posting.meta[META] = ','.join(links)
                    entry.postings[index] = new_posting
                    postings_repl_map[id(posting)] = new_posting

    # Just a sanity check.
    assert not link_map, "Internal error: not all matches found."

    # Return a list of the modified postings (mapping the old matches to the
    # newly created ones).
    return [((aug_index, postings_repl_map[id(aug_posting)]),
             (red_index, postings_repl_map[id(red_posting)]))
            for (aug_index, aug_posting), (red_index, red_posting) in all_matches]


def book_price_conversions_as_fifo(entries, assets_account, income_account):
    """Rewrite transactions to insert cost basis according to FIFO.

    See module docstring for full details.

    Args:
      entries: A list of entry instances.
      assets_account: An account string, the name of the account to process.
      income_account: An account string, the name of the account to use for booking
        realized profit/loss.
    Returns:
      A tuple of
        entries: A list of new, modified entries.
        errors: A list of errors generated by this plugin.
        matches: A list of (number, augmenting-posting, reducing-postings) for all
          matched lots.
    """
    # Pairs of (Position, Transaction) instances used to match augmenting
    # entries with reducing ones.
    fifo_lots = []

    # A list of pairs of matching (augmenting, reducing) postings.
    all_matches = []

    new_entries = []
    errors = []
    for eindex, entry in enumerate(entries):

        # Figure out if this transaction has postings in Bitcoins without a cost.
        # The purpose of this plugin is to fixup those.
        if isinstance(entry, data.Transaction) and any(is_matching(posting, assets_account)
                                                       for posting in entry.postings):

            # Segregate the reducing lots, augmenting lots and other lots.
            augmenting, reducing, other = [], [], []
            for pindex, posting in enumerate(entry.postings):
                if is_matching(posting, assets_account):
                    out = augmenting if posting.position.number >= ZERO else reducing
                else:
                    out = other
                out.append(posting)

            # We will create a replacement list of postings with costs filled
            # in, possibly more than the original list, to account for the
            # different lots.
            new_postings = []

            # Convert all the augmenting postings to cost basis.
            for posting in augmenting:
                new_postings.append(augment_inventory(fifo_lots, posting, eindex))

            # Then process reducing postings.
            if reducing:
                # Process all the reducing postings, booking them to FIFO basis.
                pnl = Inventory()
                for posting in reducing:
                    rpostings, matches, posting_pnl, new_errors = (
                        reduce_inventory(fifo_lots, posting, eindex))
                    new_postings.extend(rpostings)
                    all_matches.extend(matches)
                    errors.extend(new_errors)
                    pnl.add_amount(Amount(posting_pnl, posting.price.currency))

                # If some reducing lots were seen in this transaction, insert an
                # Income leg to absorb the P/L. We need to do this for each currency
                # which incurred P/L.
                if not pnl.is_empty():
                    for pos in pnl:
                        meta = data.new_metadata('<fifo>', 0)
                        new_postings.append(
                            data.Posting(income_account,
                                         data.Position(data.Lot(pos.lot.currency, None, None),
                                                       -pos.number),
                                         None, None,
                                         meta))

            # Third, add back all the other unrelated legs in.
            for posting in other:
                new_postings.append(posting)

            # Create a replacement entry.
            entry = entry._replace(postings=new_postings)

        new_entries.append(entry)

    # Add matching metadata to all matching postings.
    mod_matches = link_entries_with_metadata(new_entries, all_matches)

    # Resolve the indexes to their possibly modified Transaction instances.
    matches = [(data.TxnPosting(new_entries[aug_index], aug_posting),
                data.TxnPosting(new_entries[red_index], red_posting))
               for (aug_index, aug_posting), (red_index, red_posting) in mod_matches]

    return new_entries, errors, matches


def extract_trades(entries):
    """Find all the matching trades from the metadata attached to postings.

    This inspects all the postings and pairs them up using the special metadata
    field that was added by this plugin when booking matching lots, and returns
    pairs of those postings.

    Args:
      entries: The list of directives to extract from.
    Returns:
      A list of (number, augmenting-posting, reducing-posting).
    """
    trade_map = collections.defaultdict(list)
    for index, entry in enumerate(entries):
        if not isinstance(entry, data.Transaction):
            continue
        for posting in entry.postings:
            links_str = posting.meta.get(META, None)
            if links_str:
                links = links_str.split(',')
                for link in links:
                    trade_map[link].append((index, entry, posting))

    # Sort matches according to the index of the first entry, drop the index
    # used for doing this, and convert the objects to tuples..
    trades = [(data.TxnPosting(augmenting[1], augmenting[2]),
               data.TxnPosting(reducing[1], reducing[2]))
              for augmenting, reducing in sorted(trade_map.values())]

    # Sanity check.
    for matches in trades:
        assert len(matches) == 2

    return trades


def book_price_conversions_plugin(entries, options_map, config):
    """A plugin that rewrites transactions to insert cost basis according to FIFO.

    See book_price_conversions_as_fifo() for details.

    Args:
      entries: A list of entry instances.
      options_map: A dict of options parsed from the file.
      config: A string, in "<ACCOUNT1>,<ACCOUNT2>" format.
    Returns:
      A tuple of
        entries: A list of new, modified entries.
        errors: A list of errors generated by this plugin.
    """
    # The expected configuration is two account names, separated by whitespace.
    errors = []
    try:
        assets_account, income_account = re.split(r'[,; \t]', config)
    except Exception as exc:
        errors.append(
            ConfigError(None,
                        ('Invalid configuration: "{}"; '
                         'should be two accounts, skipping booking').format(config),
                        None))
        return entries, errors

    new_entries, errors, _ = book_price_conversions_as_fifo(entries,
                                                            assets_account,
                                                            income_account)
    return new_entries, errors


def main():
    """Extract trades from metadata-annotated postings and report on them.
    """
    logging.basicConfig(level=logging.INFO, format='%(levelname)-8s: %(message)s')
    parser = argparse.ArgumentParser(description=__doc__.strip())
    parser.add_argument('filename', help='Beancount input filename')

    oparser = parser.add_argument_group('Outputs')
    oparser.add_argument('-o', '--output-text', '--text', action='store',
                         help="Render results to text boxes")
    oparser.add_argument('--output-csv', '--csv', action='store',
                         help="Render results to CSV files")

    args = parser.parse_args()

    # Load the input file.
    entries, errors, options_map = loader.load_file(args.filename)

    # Get the list of trades.
    trades = extract_trades(entries)

    # Produce a table of all the trades.
    columns = ('units currency cost_currency '
               'buy_date buy_price sell_date sell_price pnl').split()
    header = ['Units', 'Currency', 'Cost Currency',
              'Buy Date', 'Buy Price', 'Sell Date', 'Sell Price',
              'P/L']
    body = []
    for aug, red in trades:
        units = -red.posting.position.number
        buy_price = aug.posting.price.number
        sell_price = red.posting.price.number
        pnl = (units * (sell_price - buy_price)).quantize(buy_price)
        body.append([
            -red.posting.position.number,
            red.posting.position.lot.currency,
            red.posting.price.currency,
            aug.txn.date.isoformat(), buy_price,
            red.txn.date.isoformat(), sell_price,
            pnl
            ])
    trades_table = table.Table(columns, header, body)

    # Render the table as text or CSV.
    table.render_table(trades_table, sys.stdout, 'text')


if __name__ == '__main__':
    main()



# FIXME: TODO
#
# - Rename this to not include "FIFO" in the name, it should be more general
#   than this, other booking methods should eventually be supported.
