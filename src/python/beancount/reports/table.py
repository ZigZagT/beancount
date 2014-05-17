"""Table rendering.
"""
import csv
import collections
import io
import itertools


# An unrendered table data structure. This is a table-like report data structure
# - a two-dimensional array of cells - that is used as an intermediate state
# between the internal data structures of Beancount and format-specific reports.
#
# Attributes:
#   columns: A sequence of strings, names for each column.
#   header: A sequence of strings, a header to be rendered for each column.
#   rows: A list of rows, each of which is a sequence of strings, the
#     contents of all the cells of the table body.
TableReport = collections.namedtuple('TableReport', 'columns header body')


def attribute_to_title(fieldname):
    """Convert programming id into readable field name.

    Args:
      fieldname: A string, a programming ids, such as 'book_value'.
    Returns:
      A readable string, such as 'Book Value.'
   """
    return fieldname.replace('_', ' ').title()


def create_table(rows, field_spec=None):
    """Convert a list of tuples to an table report object.

    Args:
      rows: A list of tuples.
      field_spec: A list of strings, or a list of (strings, header,
        formatter-function) triplets, that selects a subset of the fields is to
        be rendered as well as their ordering. If this is a dict, the values are
        functions to call on the fields to render them. If a function is set to
        None, we will just call str() on the field.
    Returns:
      A TableReport instance.
    """
    # Normalize field_spec to a dict.
    if field_spec is None:
        namedtuple_class = type(rows[0])
        field_spec = [(field, None, None)
                      for field in namedtuple_class._fields]

    elif isinstance(field_spec, (list, tuple)):
        new_field_spec = []
        for field in field_spec:
            if isinstance(field, tuple):
                assert len(field) <= 3, field
                if len(field) == 1:
                    field, = field
                    new_field_spec.append((field, None, None))
                elif len(field) == 2:
                    field, header = field
                    new_field_spec.append((field, header, None))
                elif len(field) == 3:
                    new_field_spec.append(field)
            else:
                assert isinstance(field, str), field
                new_field_spec.append((field, attribute_to_title(field), None))
        field_spec = new_field_spec

    # Ensure a nicely formatted header.
    field_spec = [((name, attribute_to_title(name), formatter)
                   if header is None
                   else (name, header, formatter))
                  for (name, header, formatter) in field_spec]

    assert isinstance(field_spec, list), field_spec
    assert all(len(x) == 3 for x in field_spec), field_spec

    # Compute the column names.
    columns = [name for (name, _, __) in field_spec]

    # Compute the table header.
    header = [header_column for (_, header_column, __) in field_spec]

    # Compute the table body.
    body = []
    for row in rows:
        body_row = []
        for name, _, formatter in field_spec:
            value = getattr(row, name)
            if value is not None:
                if formatter is not None:
                    value = formatter(value)
            else:
                value = ''
            body_row.append(value)
        body.append(body_row)

    return TableReport(columns, header, body)


def table_to_html(table, classes=None, file=None):
    """Render a TableReport to HTML.

    Args:
      table: An instance of a TableReport.
      classes: A list of string, CSS classes to set on the table.
      file: A file object to write to. If no object is provided, this
        function returns a string.
    Returns:
      A string, the rendered table, or None, if a file object is provided
      to write to.
    """
    # Initialize file.
    oss = io.StringIO() if file is None else file
    oss.write('<table class="{}">\n'.format(' '.join(classes or [])))

    # Render header.
    if table.header:
        oss.write('  <thead>\n')
        oss.write('    <tr>\n')
        for header in table.header:
            oss.write('      <th>{}</th>\n'.format(header))
        oss.write('    </tr>\n')
        oss.write('  </thead>\n')

    # Render body.
    oss.write('  <tbody>\n')
    for row in table.body:
        oss.write('    <tr>\n')
        for cell in row:
            oss.write('      <td>{}</td>\n'.format(cell))
        oss.write('    </tr>\n')
    oss.write('  </tbody>\n')

    # Render footer.
    oss.write('</table>\n')
    if file is None:
        return oss.getvalue()


def table_to_text(table,
                  column_interspace=" ",
                  formats=None):
    """Render a TableReport to ASCII text.

    Args:
      table: An instance of a TableReport.
      column_interspace: A string to render between the columns as spacer.
      formats: An optional dict of column name to a format character that gets
        inserted in a format string specified, like this (where '<char>' is):
        {:<char><width>}. A key of '*' will provide a default value, like
        this, for example: (... formats={'*': '>'}).
    Returns:
      A string, the rendered text table.
    """
    column_widths = compute_table_widths(itertools.chain([table.header],
                                                         table.body))
    num_columns = len(column_widths)

    # Insert column format chars and compute line formatting string.
    column_formats = []
    if formats:
        default_format = formats.get('*', None)
    for column, width in zip(table.columns, column_widths):
        if column and formats:
            format_ = formats.get(column, default_format)
            if format_:
                column_formats.append("{{:{}{:d}}}".format(format_, width))
            else:
                column_formats.append("{{:{:d}}}".format(width))
        else:
            column_formats.append("{{:{:d}}}".format(width))

    line_format = column_interspace.join(column_formats) + "\n"
    separator = line_format.format(*[('-' * width) for width in column_widths])

    # Render the header.
    oss = io.StringIO()
    if table.header:
        oss.write(line_format.format(*table.header))

    # Render the body.
    oss.write(separator)
    for row in table.body:
        oss.write(line_format.format(*row))
    oss.write(separator)

    return oss.getvalue()


def table_to_csv(table, file=None, **kwargs):
    """Render a TableReport to a CSV file.

    Args:
      table: An instance of a TableReport.
      file: A file object to write to. If no object is provided, this
        function returns a string.
      **kwargs: Optional arguments forwarded to csv.writer().
    Returns:
      A string, the rendered table, or None, if a file object is provided
      to write to.
    """
    output_file = file or io.StringIO()

    writer = csv.writer(output_file, **kwargs)
    if table.header:
        writer.writerow(table.header)
    writer.writerows(table.body)

    if not file:
        return output_file.getvalue()


def compute_table_widths(rows):
    """Compute the max character widths of a list of rows.

    Args:
      rows: A list of rows, which are sequences of strings.
    Returns:
      A list of integers, the maximum widths required to render the columns of
      this table.
    Raises:
      IndexError: If the rows are of different lengths.
    """
    row_iter = iter(rows)
    first_row = next(row_iter)
    num_columns = len(first_row)
    column_widths = [len(cell) for cell in first_row]
    for row in row_iter:
        for i, cell in enumerate(row):
            cell_len = len(cell)
            if cell_len > column_widths[i]:
                column_widths[i] = cell_len
        if i+1 != num_columns:
            raise IndexError("Invalid number of rows.")
    return column_widths
