# In demolition progress. See beancount.prices.prices, where this is being
# reconstructed and redesigned.
__author__ = "Martin Blais <blais@furius.ca>"

import csv
import collections
import datetime
import io
import functools
import threading
from os import path
import shelve
import tempfile
from urllib.request import urlopen
import re
import sys
import urllib
import urllib.parse
import hashlib
import argparse
import logging
from concurrent import futures

from dateutil.parser import parse as parse_datetime

from beancount import loader
from beancount.core import data
from beancount.core import amount
from beancount.ops import holdings
from beancount.parser import printer
from beancount.prices.sources import yahoo     # FIXME: remove this, should be dynamic
from beancount.prices.sources import google    # FIXME: remove this, should be dynamic


UNKNOWN_CURRENCY = '?'


def retrying_urlopen(url, timeout=5, max_retry=5):
    """Open and download the given URL, retrying if it times out.

    Args:
      url: A string, the URL to fetch.
      timeout: A timeout after which to stop waiting for a respone and return an
        error.
      max_retry: The maximum number of times to retry.
    Returns:
      The contents of the fetched URL.
    """
    for _ in range(max_retry):
        logging.info("Fetching %s", url)
        response = urlopen(url, timeout=timeout)
        if response:
            break
    if response.getcode() != 200:
        return None
    return response


def memoize_recent(function, cache_filename, date_format='%Y%m%d%H'):
    """Memoize recent calls to the given function.

    Args:
      function: A callable object.
      cache_filename: A string, the path to the database file to cache to.
      date_format: A format string for a datetime instance, that we use to hash
        into the key in order to invalidate the cache this often.
    Returns:
      A memoized version of the function.
    """
    urlcache = shelve.open(cache_filename)
    urlcache.lock = threading.Lock()  # Note: 'shelve' is not thread-safe.
    @functools.wraps(function)
    def memoized(*args, **kw):
        md5 = hashlib.md5()

        # Encode the arguments.
        md5.update(str(args).encode('utf-8'))
        md5.update(str(sorted(kw.items())).encode('utf-8'))

        # Put a date string in order to invalidate over time.
        md5.update(datetime.date.now().strftime(date_format).encode('utf-8'))

        hash_ = md5.hexdigest()

        try:
            with urlcache.lock:
                contents = urlcache[hash_]
        except KeyError:
            fileobj = function(*args, **kw)
            if fileobj:
                contents = fileobj.read()
                with urlcache.lock:
                    urlcache[hash_] = contents
            else:
                contents = None
        return io.BytesIO(contents) if contents else None
    return memoized


# A price fetching job description.
#
# Attributes:
#   source: A string identifier for a data source.
#   symbol: A ticker symbol in the universe of the source.
#   date: A datetime.date object for the date to be fetched, or None
#     with the meaning of fetching the latest price.
#   base: A currency, the base currency for the given symbol. This may be
#     null, in which case we simply issue the price value, not a price directive.
#   quote: A currency, the quote currency for the given syumbol.
#   invert: A boolean, true if we need to invert the currency.
Job = collections.namedtuple('Job', 'source symbol date base quote invert')


def parse_ticker(ticker, default_source='google'):
    """Given a ticker, parse out its components.

    The grammar follows this syntax: (SOURCE/)?(^)?SYMBOL

    For example, parsing 'MSFT' would return ('yahoo', 'MSFT', False)
    with a default_source = 'yahoo'.

    Another example is parsing 'google/^CURRENCY:USDCAD' which would
    ('google', 'CURRENCY:USDCAD', True).

    Args:
      ticker: A string, the ticker to parse.
      default_source: The default value for the source of the ticker to fetch,
        if unspecified.
    Returns:
      A tuple of
        source: A string, the source where to fetch the price from.
        symbol: A string, the unique source-specific symbol to use.
        invert: A boolean, true if we need to invert the price to be fetched.
    """
    match = re.match(r'(?:([a-z]+)/)?(\^)?([A-Z0-9:\-_]+)$', ticker)
    if match is None:
        raise ValueError('Invalid ticker: "{}"'.format(ticker))
    source, invert, symbol = match.group(1, 2, 3)
    invert = bool(invert)
    source = source or default_source
    return source, symbol, invert


def get_jobs_from_file(filename, date, default_source):
    """Given a Beancount input file, extract price fetching jobs.

    This is where we're looking at the input file and using the list of
    commodities or its history, we figure how which prices should be fetched.

    Args:
      filename: A string, the name of the file whose prices to fetch.
      date: A datetime.date instance, the date at which to extract the
        holdings for.
      default_source: A string, the name of the default source to use if
        ticker symbols don't specify one.
    Returns:
      A list of Job instances, to be processed.

    """
    entries, errors, options_map = loader.load_file(filename)

    jobs = []
    commodities_list = holdings.get_commodities_at_date(entries, options_map, date=date)
    for currency, cost_currency, quote_currency, ticker in commodities_list:
        # Ignore the commodity if it has no ticker defined on it.
        if ticker is None:
            continue
        source, symbol, invert = parse_ticker(ticker, default_source)

        # Select the quote currency if declared, otherwise use the cost
        # currency.
        base = currency
        quote = quote_currency or cost_currency

        jobs.append(Job(source, symbol, date, base, quote, invert))

    return jobs


def fetch_price(job, source_map):
    """Run the given jobs using modules from the given source map.

    Args:
      jobs: A list of Job instances.
      source_map: A mapping of source string to a source module object.
    Returns:
      A list of Price entries corresponding to the outputs of the jobs processed.
    """
    source_module = source_map[job.source]
    if job.date is None:
        srcprice = source_module.get_latest_price(job.symbol)
    else:
        srcprice = source_module.get_historical_price(job.symbol, job.date)

    if srcprice is None:
        logging.error("Could not fetch for job: %s", job)
        return

    # Invert the currencies if the rate if the rate is inverted..
    base, quote = job.base, job.quote or srcprice.quote_currency
    if job.invert:
        base, quote = quote, base

    assert base is not None
    assert quote is not None
    fileloc = data.new_metadata('<{}>'.format(type(job.source).__name__), 0)
    return data.Price(fileloc, srcprice.time.date(), base,
                      amount.Amount(srcprice.price, quote))


"""
--date
--always-invert
--all-instruments
--clobber
--debug

--cache
--no-cache
--clear-cache

"""

def process_args(argv, valid_price_sources):
    """Process command-line arguments and return a list of jobs to process.

    Args:
      argv: A list of command-line arguments. This is mainly allowed to be
        specified in explicitly for the benefit of unit tests.
      valid_price_sources: A list of strings, the names of valid price sources.
        The first item is taken to be the default price source.
    Returns:
      A list of Job tuples, a verbose boolean, and a filename string, set if we
      should use it as a cache (if None, cache is disabled).
    Raises:
      SystemExit: If something could not be parsed.

    """
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument('uri_list', nargs='+',
                        help='A list of URIs specifying which prices to fetch')

    parser.add_argument('-v', '--verbose', action='store_true',
                        help="Ouptut fetching progress log")

    parse_date = lambda s: parse_datetime(s).date()
    parser.add_argument('--date', action='store', type=parse_date,
                        help="Specify the date for which to fetch the holdings")

    parser.add_argument('--default-source', action='store', default='google',
                        choices=valid_price_sources,
                        help="Specify the default source of data for unspecified tickers.")

    filename = path.join(tempfile.gettempdir(),
                         "{}.cache.db".format(path.basename(sys.argv[0])))
    parser.add_argument('--cache', dest='cache_filename', action='store', default=filename,
                        help="Enable the cache and set the cache name")
    parser.add_argument('--no-cache', dest='cache_filename', action='store_const', const=None,
                        help="Disable the price cache")

    args = parser.parse_args(argv)

    # Prepare a list of jobs.
    jobs = []
    for uri in args.uri_list:
        parsed_uri = urllib.parse.urlparse(uri)

        # Parse an explicit price.
        if parsed_uri.scheme == 'price':
            source, symbol, invert = parse_ticker(''.join((parsed_uri.netloc,
                                                           parsed_uri.path)))
            base = symbol.split(':')[-1]
            quote = UNKNOWN_CURRENCY
            jobs.append(Job(source, symbol, args.date, base, quote, invert))

        # Parse symbols from a file.
        elif parsed_uri.scheme in ('file', ''):
            filename = parsed_uri.path
            if not (path.exists(filename) and path.isfile(filename)):
                parser.error('File does not exist: "{}"'.format(filename))
            jobs.extend(
                get_jobs_from_file(filename, args.date, args.default_source))
        else:
            parser.error('Invalid scheme "{}"'.format(parsed_uri.scheme))

    # Validate price sources.
    for job in jobs:
        if job.source not in valid_price_sources:
            parser.error('Invalid source "{}"'.format(job.source))

    return jobs, args.verbose, args.cache_filename


def main():
    # FIXME: Replace this by an importer.
    source_map = {'google': google.Source(),
                  'yahoo': yahoo.Source()}

    # Parse the arguments.
    source_names = list(source_map.keys())
    jobs, verbose, cache_filename = process_args(sys.argv[1:], source_names)
    logging.basicConfig(level=logging.INFO if verbose else logging.WARN,
                        format='%(levelname)-8s: %(message)s')

    # Install the cache.
    urllib.request.urlopen = retrying_urlopen
    if cache_filename:
        logging.info('Using cache at "{}"'.format(cache_filename))
        urllib.request.urlopen = memoize_recent(urllib.request.urlopen, cache_filename)

    # Process the jobs.
    executor = futures.ThreadPoolExecutor(max_workers=3)
    price_entries = [
        entry
        for entry in executor.map(lambda job: fetch_price(job, source_map), jobs)
        if entry is not None]

    # Print out the entries.
    printer.print_entries(price_entries)
