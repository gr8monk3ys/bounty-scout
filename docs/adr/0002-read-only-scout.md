# The scout is read-only

The scout has no code path that comments on, claims, assigns or opens a pull request for a bounty, and none should be added. Every board it targets bans automated claiming; Tenstorrent's contributing guide threatens permanent repository bans for it, and tinygrad's bounty sheet forbids it too. The scout finds work and ranks it; a human decides and does it.

Consequence: adapters only read (`gh` queries, CSV export), and any feature needing a write to a board is out of scope.
