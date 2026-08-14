// Retired: this file seeded CAUSES edges to the commodity assets GOLD and BRENT_OIL (E12 S02).
//
// WHY IT IS EMPTY
// ---------------
// Every statement here matched `(:Asset {id: 'GOLD'})` or `(:Asset {id: 'BRENT_OIL'})`. Those nodes are
// not created by 02-seed-assets.cypher: the asset registry dropped the commodity instruments in favour
// of equity proxies — NEM_NYSE (Newmont) for gold and XOM_NYSE (Exxon) for oil — because Market Data
// can price an equity and Verification can score it, and it could do neither for a bare commodity.
//
// Cypher's MATCH...MERGE is a silent no-op when the MATCH binds nothing, and cypher-shell exits 0, so
// this file's twelve expert priors were discarded at seed time with no error for as long as it existed.
// Confirmed against the live graph on 2026-08-14: no CAUSES edge targeted either id, and every weight
// observed in the 2026-08-12 prediction audit traced to a group edge in 06/07 instead.
//
// WHERE THOSE PRIORS LIVE NOW
// ---------------------------
//   * Unconditional industry priors: 06-seed-group-edges.cypher (PRECIOUS_METALS, OIL_GAS, and others).
//   * Conditioned priors: 05-seed-conditioned-edges.cypher, retargeted onto the same groups.
//
// The file is kept rather than deleted so the numbering in 01..09 stays stable and so this explanation
// sits where the next reader will look for it. 09-verify-seed.cypher now fails the seed if any
// statement targets a node id that does not exist, so this class of silent loss cannot recur.
//
// Deliberately no statements below this line.
RETURN 'retired: see 05 and 06 for these priors' AS note;
