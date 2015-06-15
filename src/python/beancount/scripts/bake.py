"""Bake a Beancount input file's web files to a directory hierarchy.

You provide a Beancount filename, an output directory, and this script
runs a server and a scraper that puts all the files in the directory,
and if your output name has an archive suffix, we automatically the
fetched directory contents to the archive and delete them.
"""
__author__ = "Martin Blais <blais@furius.ca>"

import argparse
import functools
import logging
import os
import subprocess
import shutil
import shlex
import re
from os import path
import urllib.parse

import lxml.html

from beancount.web import scrape
from beancount.web import web
from beancount.web import web_test
from beancount.scripts import checkdeps
from beancount.utils import file_utils


# Directories where binary files are allowed.
BINARY_DIRECTORIES = ['resources', 'third_party', 'doc']
BINARY_MATCH = re.compile(r'/({}/|favicon.ico$)'.format(
    '|'.join(BINARY_DIRECTORIES))).match


def normalize_filename(url):
    """Convert URL paths to filenames. Add .html extension if needed.

    Args:
      url: A string, the url to convert.
    Returns:
      A string, possibly with an extension appended.
    """
    if url.endswith('/'):
        return path.join(url, 'index.html')
    elif BINARY_MATCH(url):
        return url
    else:
        return url if url.endswith('.html') else (url + '.html')


def relativize_links(html, current_url):
    """Make all the links in the contents string relative to an URL.

    Args:
      html: An lxml document node.
      current_url: A string, the URL of the current page, a path to.
        a file or a directory. If the path represents a directory, the
        path ends with a /.
    """
    current_dir = path.dirname(current_url)
    for element, attribute, link, pos in lxml.html.iterlinks(html):
        if path.isabs(link):
            relative_link = path.relpath(normalize_filename(link), current_dir)
            element.set(attribute, relative_link)


def remove_links(html, targets):
    """Convert a list of anchors (<a>) from an HTML tree to spans (<span>).

    Args:
      html: An lxml document node.
      targets: A set of string, targets to be removed.
    """
    for element, attribute, link, pos in lxml.html.iterlinks(html):
        if link in targets:
            del element.attrib[attribute]
            element.tag = 'span'
            element.set('class', 'removed-link')


def save_scraped_document(output_dir, url, response, contents, html_root, skipped_urls):
    """Callback function to process a document being scraped.

    This converts the document to have relative links and writes out the file to
    the output directory.

    Args:
      output_dir: A string, the output directory to write.
      url: A string, the originally requested URL.
      response: An http response as per urlopen.
      contents: Bytes, the content of a response.
      html_root: An lxml root node for the document, optionally. If this is provided,
        this avoid you having to reprocess it (for performance reasons).
      skipped_urls: A set of the links from the file that were skipped.
    """
    if response.status != 200:
        logging.error("Invalid status: %s", response.status)

    # Ignore directories.
    if url.endswith('/'):
        return

    # Note that we're saving the file under the non-redirected URL, because this
    # will have to be opened using files and there are no redirects that way.

    if response.info().get_content_type() == 'text/html':
        if html_root is None:
            html_root = lxml.html.document_fromstring(contents)
        remove_links(html_root, skipped_urls)
        relativize_links(html_root, url)
        contents = lxml.html.tostring(html_root, method="html")

    # Compute output filename and write out the relativized contents.
    output_filename = path.join(output_dir,
                                normalize_filename(url).lstrip('/'))
    os.makedirs(path.dirname(output_filename), exist_ok=True)
    with open(output_filename, 'wb') as outfile:
        outfile.write(contents)


def bake_to_directory(webargs, output_dir, quiet=False):
    """Serve and bake a Beancount's web to a directory.

    Args:
      webargs: An argparse parsed options object with the web app arguments.
      output_dir: A directory name. We don't check here whether it exists or not.
      quiet: A boolean, True to suppress web server fetch log.
    Returns:
      True on success, False otherwise.
    """
    callback = functools.partial(save_scraped_document, output_dir)
    # Skip the context pages, too slow.
    # Skip the component pages... too many.
    # Skip served documents.
    processed_urls, skipped_urls = scrape.scrape(webargs.filename,
                                                 callback,
                                                 webargs.port,
                                                 '/(context|view/component|.*/doc)/',
                                                 quiet)


def archive(command_template, directory, archive, quiet=False):
    """Archive the directory to the given tar/gz archive filename.

    Args:
      command_template: A string, the command template to format with in order
        to compute the command to run.
      directory: A string, the name of the directory to archive.
      archive: A string, the name of the file to output.
      quiet: A boolean, True to suppress output.
    Raises:
      IOError: if the directory does not exist or if the archive name already
      exists.

    """
    directory = path.abspath(directory)
    archive = path.abspath(archive)
    if not path.exists(directory):
        raise IOError("Directory to archive '{}' does not exist".format(
            directory))
    if path.exists(archive):
        raise IOError("Output archive name '{}' already exists".format(
            archive))

    command = command_template.format(directory=directory,
                                      dirname=path.dirname(directory),
                                      basename=path.basename(directory),
                                      archive=archive)

    pipe = subprocess.Popen(shlex.split(command),
                            shell=False,
                            cwd=path.dirname(directory),
                            stdout=subprocess.PIPE if quiet else None,
                            stderr=subprocess.PIPE if quiet else None)
    _, _ = pipe.communicate()
    if pipe.returncode != 0:
        raise OSError("Archive failure")


ARCHIVERS = {
    '.tar.gz'  : 'tar -C {dirname} -zcvf {archive} {basename}',
    '.tgz'     : 'tar -C {dirname} -zcvf {archive} {basename}',
    '.tar.bz2' : 'tar -C {dirname} -jcvf {archive} {basename}',
    '.zip'     : 'zip -r {archive} {basename}',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)

    web_group = web.add_web_arguments(parser)
    web_group.set_defaults(port=9475)

    group = parser.add_argument_group("Bake process arguments")

    group.add_argument('output',
                       help=('The output directory or archive name. If you '
                             'specify a filename with a well-known extension,'
                             'we automatically archive the fetched directory '
                             'contents to this archive name and delete them.'))

    group.add_argument('-q', '--quiet', action='store_true',
                       help="Don't even print out web server log")

    opts = parser.parse_args()

    # Figure out the archival method.
    output_directory, extension = file_utils.path_greedy_split(opts.output)
    if extension:
        try:
            archival_command = ARCHIVERS[extension]
        except KeyError:
            raise SystemExit("ERROR: Unknown archiver type '{}'".format(extension))
    else:
        archival_command = None

    # Check pre-conditions on input/output filenames.
    if not path.exists(opts.filename):
        raise SystemExit("ERROR: Missing input file '{}'".format(opts.filename))
    if path.exists(opts.output):
        raise SystemExit("ERROR: Output path already exists '{}'".format(opts.output))
    if path.exists(output_directory):
        raise SystemExit(
            "ERROR: Output directory already exists '{}'".format(output_directory))

    # Bake to a directory hierarchy of files with local links.
    bake_to_directory(opts, output_directory, opts.quiet)

    # Verify the bake output files. This is just a sanity checking step.
    # You can also use "bean-doctor validate_html <file> to run this manually.
    logging.info('Validating HTML output files & links.')
    files, missing, empty = scrape.validate_local_links_in_dir(output_directory)
    logging.info('Validation: %d files processed', len(files))
    for target in missing:
        logging.error("Validation error: Missing '%s'", target)
    for target in empty:
        logging.error("Validation error: Empty '%s'", target)

    # Archive if requested.
    if archival_command:
        archive(archival_command, output_directory, opts.output, True)
        shutil.rmtree(output_directory)

    print("Output in '{}'".format(opts.output))


if __name__ == '__main__':
    main()
