import language_tool_python
import sys, fileinput

def lint(data):
    tool = language_tool_python.LanguageTool('en-US')
    matches = tool.check(data)
    print(f"Total Mistakes: {len(matches)}")

    for match in matches:
        print(match)

def from_input():
    data = sys.stdin.read().strip()
    lint(data)

def from_files(files):
    with fileinput.input(files) as f:
        data = " ".join(line.strip() for line in f)
    lint(data)

if __name__ == "__main__":
    if not sys.stdin.isatty():
        from_input()
    elif len(sys.argv) > 1:
        from_files(sys.argv[1:])
    else:
        print("Please provide input via stdin, or provide files as arguments")
