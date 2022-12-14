import argparse
import csv
from itertools import count
import sys
from datetime import datetime
from io import StringIO
from dgh import CsvDGH


_DEBUG = True


class _Table:

    def __init__(self, pt_path: str, dgh_paths: dict):

        """
        Instantiates a table and the specified Domain Generalization Hierarchies from the
        corresponding files.

        :param pt_path:             Path to the table to anonymize.
        :param dgh_paths:           Dictionary whose values are paths to DGH files and whose keys
                                    are the corresponding attribute names.
        :raises IOError:            If a file cannot be read.
        :raises FileNotFoundError:  If a file cannot be found.
        """

        self.table = None
        """
        Reference to the table file.
        """
        self.attributes = dict()
        """
        Dictionary whose keys are the table attributes names and whose values are the corresponding
        column indices.
        """
        self._init_table(pt_path)
        """
        Reference to the table file.
        """
        self.dghs = dict()
        """
        Dictionary whose values are DGH instances and whose keys are the corresponding attribute 
        names.
        """
        for attribute in dgh_paths:
            self._add_dgh(dgh_paths[attribute], attribute)

    def __del__(self):

        """
        Closes the table file.
        """

        self.table.close()

    def compute_count(self, freq, k):
            count=0
            for currTup in freq.values():
                if currTup[1] < k:
                    count += currTup[1]

            return count

    def anonymize(self, qi_names: list, k: int, output_path: str, v=True):

        """
        Writes a k-anonymous representation of this table on a new file. The maximum number of
        suppressed rows is k.

        :param qi_names:    List of names of the Quasi Identifiers attributes to consider during
                            k-anonymization.
        :param k:           Level of anonymity.
        :param output_path: Path to the output file.
        :param v:           If True prints some logging.
        :raises KeyError:   If a QI attribute name is not valid.
        :raises IOError:    If the output file cannot be written.
        """

        global _DEBUG

        if v:
            _DEBUG = False

        self._debug("[DEBUG] Creating the output file...", _DEBUG)
        try:
            output = open(output_path, 'w')
        except IOError:
            raise
        self._log("[LOG] Created output file.", endl=True, enabled=v)

        # Start reading the table file from the top:
        self.table.seek(0)

        self._debug("[DEBUG] Instantiating the QI frequency dictionary...", _DEBUG)
        # Dictionary whose keys are sequences of values for the Quasi Identifiers and whose values
        # are couples (n, s) where n is the number of occurrences of a sequence and s is a set
        # containing the indices of the rows in the original table file with those QI values:
        qi_frequency = dict()

        self._debug("[DEBUG] Instantiating the attributes domains dictionary...", _DEBUG)
        domains = dict()
        for i, attribute in enumerate(qi_names):
            domains[i] = set()

        # Dictionary whose keys are the indices in the QI attribute names list, and whose values are
        # the current levels of generalization, from 0 (not generalized):
        gen_levels = dict()
        for i, attribute in enumerate(qi_names):
            gen_levels[i] = 0


        # 1. build a frequency dict freq with quasi identifier where
        # key = distinct values of PT[QI]
        # value = number of occurrences of each combination of values        
        for idx, row in enumerate(self.table):
            qi_sequence = self._get_values(row, list(qi_names), idx)
            if not qi_sequence: continue
            if tuple(qi_sequence) in qi_frequency:
                currTup = qi_frequency[tuple(qi_sequence)]
                currList = currTup[0]
                currList.append(idx)
                qi_frequency[tuple(qi_sequence)] = tuple([currList, currTup[1]+1])

            else:
                qi_frequency[tuple(qi_sequence)] = tuple([[idx], 1])
                # Update domain set for each attribute in this sequence:
                for j, value in enumerate(qi_sequence):
                    domains[j].add(value)
                
        self._debug("[DEBUG] domains is: " + str(domains), _DEBUG)
        self._debug("[DEBUG] gen_levels is: " + str(gen_levels), _DEBUG)
        self._debug("[DEBUG] Dictionary is: " + str(qi_frequency), _DEBUG)
        
        # 2. considering all rows and their values, if there's a value < k add it to count
        # and if count > k i have to go on
        while self.compute_count(qi_frequency, k) > k:  
            self._debug("[DEBUG] compute_count is: " + str(self.compute_count(qi_frequency, k)), _DEBUG)

            # Get the attribute whose domain has the max cardinality:
            max_cardinality, max_attribute_idx = 0, None

            for attribute_idx in domains:
                if len(domains[attribute_idx]) > max_cardinality:
                    max_cardinality = len(domains[attribute_idx])
                    max_attribute_idx = attribute_idx

            # Index of the attribute to generalize:
            attribute_idx = max_attribute_idx
            self._debug("[DEBUG] Attribute to generalize is: " + str(attribute_idx), _DEBUG)

            # Generalize each value for that attribute and update the attribute set in the domains dictionary:
            domains[attribute_idx] = set()
            # Look up table for the generalized values, to avoid searching in hierarchies:
            generalizations = dict()

            # Note: using the list of keys since the dictionary is changed in size at runtime
            # and it can't be used an iterator:
            for j, qi_sequence in enumerate(list(qi_frequency)):

                # Get the generalized value:
                if qi_sequence[attribute_idx] in generalizations:
                    # Find directly the generalized value in the look up table:
                    generalized_value = generalizations[attribute_idx]
                else:
                    # Get the corresponding generalized value from the attribute DGH:
                    try:
                        generalized_value = self.dghs[qi_names[attribute_idx]].generalize(qi_sequence[attribute_idx],gen_levels[attribute_idx]) 
                    except KeyError as error:
                        output.close()
                        return

                    # Skip if it's a hierarchy root:    
                    if generalized_value is None: continue

                    # Add to the look up table:
                    generalizations[attribute_idx] = generalized_value

                # Update the tuples with generalized value
                new_qi_sequence = list(qi_sequence)
                new_qi_sequence[attribute_idx] = generalized_value
                new_qi_sequence = tuple(new_qi_sequence)
                
                # Check if there is already a tuple like this one and update it
                if new_qi_sequence in qi_frequency:
                    occurrences = qi_frequency[new_qi_sequence][1] + qi_frequency[qi_sequence][1]

                    rows_set = set(qi_frequency[new_qi_sequence][0]).union(set(qi_frequency[qi_sequence][0]))
                    qi_frequency[new_qi_sequence] = (list(rows_set), occurrences)
                    
                    # Remove the old sequence:
                    qi_frequency.pop(qi_sequence)
                
                else:
                    # Add new tuple and remove the old one:
                    qi_frequency[new_qi_sequence] = qi_frequency.pop(qi_sequence)

                # Update domain set with this attribute value:
                domains[attribute_idx].add(qi_sequence[attribute_idx])

            # Update current level of generalization:
            gen_levels[attribute_idx] += 1


        # 3. delete rows with occurences less than k
        # Drop tuples which occur less than k times:
        toRem = set()
        for qi_sequence, data in qi_frequency.items():
            if data[1] < k:
                toRem.add(qi_sequence)

        while toRem:
            elem = toRem.pop()
            del qi_frequency[elem]
            

        # 4. Updating and publishing the anonymized table
        self.table.seek(0)

        for i, row in enumerate(self.table):
            table_row = self._get_values(row, list(self.attributes), i)
            if table_row is None: continue

            # Find sequence corresponding to this row index:
            for qi_sequence in qi_frequency:
                if i in qi_frequency[qi_sequence][0]:
                    line = self._set_values(table_row, qi_sequence, qi_names)
                    print(line, file=output, end="")

        output.close()

        self._log("[LOG] All done.", endl=True, enabled=v)

    

    @staticmethod
    def _log(content, enabled=True, endl=True):

        """
        Prints a log message.

        :param content: Content of the message.
        :param enabled: If False the message is not printed.
        """

        if enabled:
            if endl:
                print(content)
            else:
                sys.stdout.write('\r' + content)

    @staticmethod
    def _debug(content, enabled=False):

        """
        Prints a debug message.

        :param content: Content of the message.
        :param enabled: If False the message is not printed.
        """

        if enabled:
            print(content)

    def _init_table(self, pt_path: str):

        """
        Gets a reference to the table file and instantiates the attribute dictionary.

        :param pt_path:             Path to the table file.
        :raises IOError:            If the file cannot be read.
        :raises FileNotFoundError:  If the file cannot be found.
        """

        try:
            self.table = open(pt_path, 'r')
        except FileNotFoundError:
            raise

    def _get_values(self, row: str, attributes: list, row_index=None):

        """
        Gets the values corresponding to the given attributes from a row.

        :param row:         Line of the table file.
        :param attributes:  Names of the attributes to get the data of.
        :param row_index:   Index of the row in the table file.
        :return:            List of corresponding values if valid, None if this row must be ignored.
        :raises KeyError:   If an attribute name is not valid.
        :raises IOError:    If the row cannot be read.
        """

        # Ignore empty lines:
        if row.strip() == '':
            return None

    def _set_values(self, row, values, attributes: list) -> str:

        """
        Sets the values of a row for the given attributes and returns the row as a formatted string.

        :param row:         List of values of the row.
        :param values:      Values to set.
        :param attributes:  Names of the attributes to set.
        :return:            The new row as a formatted string.
        """

        pass

    def _add_dgh(self, dgh_path: str, attribute: str):

        """
        Adds a Domain Generalization Hierarchy to this table DGH collection, from its file.

        :param dgh_path:            Path to the DGH file.
        :param attribute:           Name of the attribute with this DGH.
        :raises IOError:            If the file cannot be read.
        :raises FileNotFoundError:  If the file cannot be found.
        """

        pass


class CsvTable(_Table):

    def __init__(self, pt_path: str, dgh_paths: dict):

        super().__init__(pt_path, dgh_paths)

    def __del__(self):

        super().__del__()

    def anonymize(self, qi_names, k, output_path, v=False):

        super().anonymize(qi_names, k, output_path, v)

    def _init_table(self, pt_path):

        super()._init_table(pt_path)

        try:
            # Try to read the first line (which contains the attribute names):
            csv_reader = csv.reader(StringIO(next(self.table)))
        except IOError:
            raise

        # Initialize the dictionary of table attributes:
        for i, attribute in enumerate(next(csv_reader)):
            self.attributes[attribute] = i

    def _get_values(self, row: str, attributes: list, row_index=None):

        super()._get_values(row, attributes, row_index)

        # Ignore the first line (which contains the attribute names):
        if row_index is not None and row_index == 0:
            return None

        # Try to parse the row:
        try:
            csv_reader = csv.reader(StringIO(row))
        except IOError:
            raise
        parsed_row = next(csv_reader)

        values = list()
        for attribute in attributes:
            if attribute in self.attributes:
                values.append(parsed_row[self.attributes[attribute]])
            else:
                raise KeyError(attribute)

        return values

    def _set_values(self, row: list, values, attributes: list):

        for i, attribute in enumerate(attributes):
            row[self.attributes[attribute]] = values[i]

        values = StringIO()
        csv_writer = csv.writer(values)
        csv_writer.writerow(row)

        return values.getvalue()

    def _add_dgh(self, dgh_path, attribute):

        try:
            self.dghs[attribute] = CsvDGH(dgh_path)
        except FileNotFoundError:
            raise
        except IOError:
            raise


if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description="Python implementation of the Datafly algorithm. Finds a k-anonymous "
                    "representation of a table.")
    parser.add_argument("--private_table", "-pt", required=True,
                        type=str, help="Path to the CSV table to K-anonymize.")
    parser.add_argument("--quasi_identifier", "-qi", required=True,
                        type=str, help="Names of the attributes which are Quasi Identifiers.",
                        nargs='+')
    parser.add_argument("--domain_gen_hierarchies", "-dgh", required=True,
                        type=str, help="Paths to the generalization files (must have same order as "
                                       "the QI name list.",
                        nargs='+')
    parser.add_argument("-k", required=True,
                        type=int, help="Value of K.")
    parser.add_argument("--output", "-o", required=True,
                        type=str, help="Path to the output file.")
    args = parser.parse_args()

    try:

        start = datetime.now()

        dgh_paths = dict()
        for i, qi_name in enumerate(args.quasi_identifier):
            dgh_paths[qi_name] = args.domain_gen_hierarchies[i]
        table = CsvTable(args.private_table, dgh_paths)
        try:
            table.anonymize(args.quasi_identifier, args.k, args.output, v=False)
        except KeyError as error:
            if len(error.args) > 0:
                _Table._log("[ERROR] Quasi Identifier '%s' is not valid." % error.args[0],
                            endl=True, enabled=True)
            else:
                _Table._log("[ERROR] A Quasi Identifier is not valid.", endl=True, enabled=True)

        end = (datetime.now() - start).total_seconds()
        _Table._log("[LOG] Done in %.2f seconds (%.3f minutes (%.2f hours))" %
                    (end, end / 60, end / 60 / 60), endl=True, enabled=True)

    except FileNotFoundError as error:
        _Table._log("[ERROR] File '%s' has not been found." % error.filename,
                    endl=True, enabled=True)
    except IOError as error:
        _Table._log("[ERROR] There has been an error with reading file '%s'." % error.filename,
                    endl=True, enabled=True)
