# AvianVisitors (Iberian fork)

A fork of [AvianVisitors](https://github.com/Twarner491/AvianVisitors) with an illustration set generated for the Iberian Peninsula.

![Iberian illustration set](/PREVIEW.png)

## Branches

| Branch | Purpose |
|---|---|
| `avian-visitors` | Clean mirror of upstream. No changes of my own, kept in sync as reference. |
| `iberia-illustrations` | **The bundle.** Upstream plus the Iberian illustration set, nothing else. This is the branch to grab if you want the illustrations. |
| `feature/*` | Short lived branches, one per pull request to upstream. Deleted once merged. |

## The illustration set

`iberia-illustrations` adds around 440 species covering Madrid and Castilla-La Mancha, filtered by eBird regional species lists (`ES-MD`, `ES-CM`) and intersected with the BirdNET label set, so every species included is one the detector can actually report.

It also includes established introduced species that are genuinely detectable here, such as Monk Parakeet (*Myiopsitta monachus*) and Rose-ringed Parakeet (*Psittacula krameri*), both with breeding populations in Madrid.

## Work in progress

This set is not finished. Right now it covers the **Madrid** and **Castilla-La Mancha** eBird regional lists, with partial coverage of the wider Iberian list, and the goal is to eventually round out the whole Peninsula, Portugal included.

Contributions are very welcome. If you run AvianVisitors in Iberia and generate species that are missing here, or better renders of ones that already are, feel free to open a pull request against `iberia-illustrations`. Regenerating a bird that came out poorly counts just as much as adding a new one.
