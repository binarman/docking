#/usr/bin/python3
# TODO
# do not replace dot op embedded encodings
# parse commands from comments: custom name, drop line, drop definition, drop types
# option to drop locations
# option to replace all values, except selected
import sys
import re

def print_help():
  print("Usage: lit_gen < <input> > <output>")

class MLIRParser:
  def reset_state(self):
    self.values = {}
    self.encodings = {}

  def __init__(self):
    self.reset_state()
    # analyze value definitions
    # id-punct  ::= [$._-]
    # suffix-id ::= (digit+ | ((letter|id-punct) (letter|id-punct|digit)*))
    # value-id  ::= `%` suffix-id
    self.value_name_pattern = "(%(\\d+|[a-zA-Z$._\\-][a-zA-Z0-9$._\\-]*))(:[0-9]+)?"
    # alias-name ::= `#` (letter|[_]) (letter|digit|[_$.])*
    self.encoding_name_pattern = "(#[a-zA-Z_][a-zA-Z0-9_$.]*)"

  def define_new_encoding(self, orig_name):
    id = len(self.encodings)
    new_name = "$ENCODING_" + str(id)
    self.encodings[orig_name] = "[[" + new_name + "]]"
    return new_name

  def define_new_value(self, orig_name):
    id = len(self.values)
    new_name = "VALUE_" + str(id)
    self.values[orig_name] = "[[" + new_name + "]]"
    return new_name

  def escape_special_sequences(self, line):
    processed_line = re.sub(r"\[\[", r"{{\[\[}}", line)
    processed_line = re.sub(r"\]\]", r"{{\[\[}}", processed_line)
    return processed_line

  def replace_all_instances(self, line, pattern, replacements):
    pat = re.compile(pattern)
    pos = 0
    result_line = ""
    while match := pat.search(line, pos):
      result_line += line[pos:match.start(1)]
      name = match[1]
      if name in replacements:
        result_line += replacements[name]
      else:
        result_line += "{{.*}}"
      pos = match.end(1)
    result_line += line[pos:]
    return result_line

  def replace_all_encodings(self, line):
    pat = re.compile(self.encoding_name_pattern)
    pos = 0
    result_line = ""
    while match := pat.search(line, pos):
      result_line += line[pos:match.start(1)]
      # skip in place declaration of encoding
      name = match[1]
      if line[match.end(1):].strip().startswith("<"):
        result_line += name
        pos = match.end(1)
        continue
      if name in self.encodings:
        result_line += self.encodings[name]
      else:
        result_line += "{{.*}}"
      pos = match.end(1)
    result_line += line[pos:]
    return result_line

  def is_func(self, line):
    return re.match("^ *tt\\.func", line)

  def is_block(self, line):
    # block-label ::= `^` (digit+ | ((letter|id-punct) (letter|id-punct|digit)*)) block-arg-list? `:`
    return re.match(r"^ *\^([0-9]+|[a-zA-Z$._\-][a-zA-Z0-9$._\-]*)(\(.*\))?:", line)

  def process_value_definitions(self, line):
    if self.is_func(line) or self.is_block(line):
      pattern = re.compile(self.value_name_pattern)
    else:
      pattern = re.compile(self.value_name_pattern + " *=")
    pos = 0
    processed_line = ""
    while match := pattern.search(line, pos):
      orig_name = match.group(1)
      replacement_name = self.define_new_value(orig_name)
      processed_line += line[pos:match.start(1)] + "[[" + replacement_name + ":%.*]]"
      pos = match.end(1)
    processed_line += line[pos:]
    return processed_line

  def process_encoding_definition(self, line):
    match = re.match("^ *" + self.encoding_name_pattern, line)
    orig_name = match.group(1)
    replacement_name = self.define_new_encoding(orig_name)
    processed_line = line[:match.start(1)] + "[[" + replacement_name + ":#.*]]" + line[match.end(1):]
    return processed_line

  def attach_check(self, line):
    if re.match("^ *tt\\.func", line):
      return "CHECK-LABEL: " + line
    elif line.strip() != "":
      return "CHECK-DAG: " + line
    else:
      return ""

  def process_line(self, line) -> str:
    if line.strip().startswith("// -----"):
      self.reset_state()
      return line

    processed_line = self.escape_special_sequences(line)

    # analyze encoding definitions
    if re.match("^ *#", line):
      processed_line = self.process_encoding_definition(processed_line)
    else:
      processed_line = self.process_value_definitions(line)
      processed_line = self.replace_all_encodings(processed_line)
      # add wrapping pattern to avoid replacement of value definition: [[VALUE:%.*]]
      processed_line = self.replace_all_instances(processed_line, "[^:]" + self.value_name_pattern + "[^\\]]", self.values)

    processed_line = self.attach_check(processed_line)

    return processed_line

if __name__ == "__main__":
  if len(sys.argv) > 1:
    print_help()
    exit(1)
  parser = MLIRParser()
  for line in sys.stdin:
    print(parser.process_line(line.rstrip()))
