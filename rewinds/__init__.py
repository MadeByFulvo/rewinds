# rewinds: run any file from any commit, without touching your working tree.
#
# The pieces:
#   extract.py   get a commit's files into a temp dir
#   runner.py    execute one of those files, reproducibly
#   venv.py      build a throwaway environment from a historical manifest
#   refs.py      resolve time-based refs like @2026-06-01
#   bisect.py    find the commit that changed a file's behavior
#   cli.py       the rewinds command

__version__ = "0.2.0"
