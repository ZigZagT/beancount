"""Compute unrealized gains.
"""
import collections

from beancount.core.amount import D, ZERO
from beancount.core import data
from beancount.core import account
from beancount.core import getters
from beancount.core.data import Transaction, Posting, Source
from beancount.core.position import Lot, Position
from beancount.core import flags
from beancount.ops import holdings
from beancount.ops import prices
from beancount.parser import options


__plugins__ = ('add_unrealized_gains',)


UnrealizedError = collections.namedtuple('UnrealizedError', 'source message entry')


def add_unrealized_gains__new(entries, options_map, subaccount=None):
    """Insert entries for unrealized capital gains.

    This function inserts entries that represent unrealized gains, at the end of
    the available history. It returns a new list of entries, with the new gains
    inserted. It replaces the account type with an entry in an income account.
    Optionally, it can book the gain in a subaccount of the original and income
    accounts.

    Args:
      entries: A list of data directives.
      options_map: A dict of options, that confirms to beancount.parser.options.
      subaccount: A string, and optional the name of a subaccount to create
        under an account to book the unrealized gain. If this is left to its
        default value, the gain is booked directly in the same account.
    Returns:
      A list of entries, which includes the new unrealized capital gains entries
      at the end, and a list of errors. The new list of entries is still sorted.
    """
    errors = []
    source = Source('<unrealized_gains>', 0)

    account_types = options.get_account_types(options_map)

    # Assert the subaccount name is in valid format.
    if subaccount:
        validation_account = account.join(account_types.assets, subaccount)
        if not account.is_valid(validation_account):
            errors.append(
                UnrealizedError(source,
                                "Invalid subaccount name: '{}'".format(subaccount),
                                None))
            return entries, errors

    if not entries:
        return (entries, errors)

    # Group positions by (account, cost, cost_currency).
    price_map = prices.build_price_map(entries)
    holdings_list = holdings.get_final_holdings(entries, price_map=price_map)

    # Group positions by (account, cost, cost_currency).
    holdings_list = holdings.aggregate_holdings_by(
        holdings_list, lambda h: (h.account, h.currency, h.cost_currency))

    # Get the latest prices from the entries.
    price_map = prices.build_price_map(entries)

    # Create transactions to account for each position.
    new_entries = []
    latest_date = entries[-1].date
    for index, holding in enumerate(holdings_list):
        if (holding.currency == holding.cost_currency or
            holding.cost_currency is None):
            continue

        # Note: since we're only considering positions held at cost, the
        # transaction that created the position *must* have created at least one
        # price point for that commodity, so we never expect for a price not to
        # be available, which is reasonable.
        if holding.price_number is None:
            errors.append(
                UnrealizedError(source,
                                "A valid price for {h.currency}/{h.cost_currency} "
                                "could not be found.".format(h=holding), None))
            continue

        # Compute the PnL; if there is no profit or loss, we create a
        # corresponding entry anyway.
        pnl = holding.market_value - holding.book_value
        if holding.number == ZERO:
            # If the number of units sum to zero, the holdings should have been
            # zero.
            errors.append(
                UnrealizedError(source,
                                "Number of units of {} in {} in holdings sum to zero "
                                "for account {} and should not.".format(
                                    currency, cost_currency, account_name),
                                None))
            continue

        # Compute the name of the accounts and add the requested subaccount name
        # if requested.
        asset_account = holding.account
        income_account = account.join(account_types.income,
                                      account.sans_root(holding.account))
        if subaccount:
            asset_account = account.join(asset_account, subaccount)
            income_account = account.join(income_account, subaccount)

        # Create a new transaction to account for this difference in gain.
        gain_loss_str = "gain" if pnl > ZERO else "loss"
        narration = ("Unrealized {} for {h.number} units of {h.currency} "
                     "(price: {h.price_number:.4f} {h.cost_currency} as of {h.price_date}, "
                     "average cost: {h.cost_number:.4f} {h.cost_currency})").format(
                         gain_loss_str, h=holding)
        entry = Transaction(source._replace(lineno=1000 + index),
                            latest_date, flags.FLAG_UNREALIZED,
                            None, narration, None, None, [])

        # Book this as income, converting the account name to be the same, but as income.
        # Note: this is a rather convenient but arbitraty choice--maybe it would be best to
        # let the user decide to what account to book it, but I don't a nice way to let the
        # user specify this.
        #
        # Note: we never set a price because we don't want these to end up in Conversions.
        entry.postings.extend([
            Posting(entry, asset_account,
                    Position(Lot(holding.cost_currency, None, None), pnl),
                    None,
                    None),
            Posting(entry, income_account,
                    Position(Lot(holding.cost_currency, None, None), -pnl),
                    None,
                    None)
        ])

        new_entries.append(entry)

    ## FIXME: remove
    from beancount.parser import printer
    with open('/tmp/unrealized.beancount.new', 'a') as f:
        printer.print_entries(new_entries, file=f)

    # Ensure that the accounts we're going to use to book the postings exist, by
    # creating open entries for those that we generated that weren't already
    # existing accounts.
    new_accounts = {posting.account
                    for entry in new_entries
                    for posting in entry.postings}
    open_entries = getters.get_account_open_close(entries)
    new_open_entries = []
    for account_ in sorted(new_accounts):
        if account_ not in open_entries:
            open_entry = data.Open(source._replace(lineno=index),
                                   latest_date, account_, None)
            new_open_entries.append(open_entry)

    return (entries + new_open_entries + new_entries, errors)


def add_unrealized_gains__old(entries, options_map, subaccount=None):
    """Insert entries for unrealized capital gains.

    This function inserts entries that represent unrealized gains, at the end of
    the available history. It returns a new list of entries, with the new gains
    inserted. It replaces the account type with an entry in an income account.
    Optionally, it can book the gain in a subaccount of the original and income
    accounts.

    Args:
      entries: A list of data directives.
      options_map: A dict of options, that confirms to beancount.parser.options.
      subaccount: A string, and optional the name of a subaccount to create
        under an account to book the unrealized gain. If this is left to its
        default value, the gain is booked directly in the same account.
    Returns:
      A list of entries, which includes the new unrealized capital gains entries
      at the end, and a list of errors. The new list of entries is still sorted.
    """
    errors = []
    source = Source('<unrealized_gains>', 0)

    account_types = options.get_account_types(options_map)

    # Assert the subaccount name is in valid format.
    if subaccount:
        validation_account = account.join(account_types.assets, subaccount)
        if not account.is_valid(validation_account):
            errors.append(
                UnrealizedError(source,
                                "Invalid subaccount name: '{}'".format(subaccount),
                                None))
            return entries, errors

    if not entries:
        return (entries, errors)

    # Group positions by (account, cost, cost_currency).
    account_holdings = collections.defaultdict(list)
    for holding in holdings.get_final_holdings(entries):
        # Skip
        if (holding.currency == holding.cost_currency or
            not holding.cost_currency):
            continue
        key = (holding.account,
               holding.currency,
               holding.cost_currency)
        account_holdings[key].append(holding)

    # Sort the holdings, to order synthesized file locations, in order to
    # produce a stable output.
    sorted_holdings = sorted(account_holdings.items())

    # Get the latest prices from the entries.
    price_map = prices.build_price_map(entries)

    # Create transactions to account for each position.
    new_entries = []
    latest_date = entries[-1].date
    for index, ((account_name,
                 currency,
                 cost_currency), holdings_list) in enumerate(sorted_holdings):

        # Get the price of this currency/cost pair.
        try:
            price_date, price_number = prices.get_price(price_map,
                                                        (currency, cost_currency),
                                                        latest_date)
        except KeyError:
            price_number = None

        # Note: since we're only considering positions held at cost, the
        # transaction that created the position *must* have created at least one
        # price point for that commodity, so we never expect for a price not to
        # be available, which is reasonable.
        if price_number is None:
            errors.append(
                UnrealizedError(source,
                                "A valid price for {}/{} could not be found.".format(
                                    currency, cost_currency), None))
            continue

        # Compute the total number of units and book value for set of positions.
        total_units = D()
        market_value = D()
        book_value = D()
        for holding in holdings_list:
            units = holding.number
            total_units += units
            if holding.market_value:
                market_value += holding.market_value
            else:
                market_value += units * price_number
            if holding.book_value:
                book_value += holding.book_value
            else:
                book_value += units * holding.cost_number

        # Compute the PnL; if there is no profit or loss, we create a
        # corresponding entry anyway.
        pnl = market_value - book_value
        if total_units == ZERO:
            # If the number of units sum to zero, the holdings should have been
            # zero.
            errors.append(
                UnrealizedError(source,
                                "Number of units of {} in {} in holdings sum to zero "
                                "for account {} and should not.".format(
                                    currency, cost_currency, account_name),
                                None))
            continue
        average_cost = book_value / total_units

        # Compute the name of the accounts and add the requested subaccount name
        # if requested.
        asset_account = account_name
        income_account = account.join(account_types.income,
                                      account.sans_root(account_name))
        if subaccount:
            asset_account = account.join(asset_account, subaccount)
            income_account = account.join(income_account, subaccount)

        # Create a new transaction to account for this difference in gain.
        gain_loss_str = "gain" if pnl > ZERO else "loss"
        narration = ("Unrealized {} for {} units of {} "
                     "(price: {:.4f} {} as of {}, "
                     "average cost: {:.4f} {})").format(
                         gain_loss_str, total_units, currency,
                         price_number, cost_currency, price_date,
                         average_cost, cost_currency)
        entry = Transaction(source._replace(lineno=1000 + index),
                            latest_date, flags.FLAG_UNREALIZED,
                            None, narration, None, None, [])

        # Book this as income, converting the account name to be the same, but as income.
        # Note: this is a rather convenient but arbitraty choice--maybe it would be best to
        # let the user decide to what account to book it, but I don't a nice way to let the
        # user specify this.
        #
        # Note: we never set a price because we don't want these to end up in Conversions.
        entry.postings.extend([
            Posting(entry, asset_account,
                    Position(Lot(cost_currency, None, None), pnl),
                    None,
                    None),
            Posting(entry, income_account,
                    Position(Lot(cost_currency, None, None), -pnl),
                    None,
                    None)
        ])

        new_entries.append(entry)

    ## FIXME: remove
    from beancount.parser import printer
    with open('/tmp/unrealized.beancount.old', 'a') as f:
        printer.print_entries(new_entries, file=f)

    # Ensure that the accounts we're going to use to book the postings exist, by
    # creating open entries for those that we generated that weren't already
    # existing accounts.
    new_accounts = {posting.account
                    for entry in new_entries
                    for posting in entry.postings}
    open_entries = getters.get_account_open_close(entries)
    new_open_entries = []
    for account_ in sorted(new_accounts):
        if account_ not in open_entries:
            open_entry = data.Open(source._replace(lineno=index),
                                   latest_date, account_, None)
            new_open_entries.append(open_entry)

    return (entries + new_open_entries + new_entries, errors)


def get_unrealized_entries(entries):
    """Return entries automatically created for unrealized gains.

    Args:
      entries: A list of directives.
    Returns:
      A list of directives, all of which are in the original list.
    """
    return [entry
            for entry in entries
            if (isinstance(entry, data.Transaction) and
                entry.flag == flags.FLAG_UNREALIZED)]


add_unrealized_gains = add_unrealized_gains__new
