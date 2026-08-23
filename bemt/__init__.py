"""BEMT - Benji Eve Market Tool.

Reads a character's own sell orders (and, optionally, station hangar stock) and
turns them into an in-game multibuy list of what needs re-stocking.

Deliberately standalone: this package imports nothing from AEMT, so the folder
can be copied to another machine and run on its own.
"""

__version__ = "0.1.0"
