# Portal notes (scraping team, informal)

These are working notes, not a specification. They were written by three
different people over two years. Where they contradict the data, trust
the data -- but they will usually tell you *why* the data looks like it does.

## The nodal aggregators -- read this one first

`P001` `P002` `P003` `P004` `P005` `P006` are not procuring entities. They
are aggregation services that re-publish notices on behalf of departments,
and between them they account for a large fraction of everything we scrape.

Two things about them that have bitten us:

1. **They paste the same legal preamble onto every single notice.** P001,
   P002 and P005 use the ~1,400 character 'NATIONAL PROCUREMENT AGGREGATION
   SERVICE' block. P003, P004 and P006 use the 'STATE PROCUREMENT CELL'
   block, which is about the same size. We strip nothing -- the body column
   is exactly what the page contained.
2. **A short notice from one of these portals is mostly preamble.** We have
   entries where the actual tender text is under 600 characters sitting
   under 1,400 characters of boilerplate. Somebody on the ops team once
   complained that 'everything on P001 looks like everything else on P001'.
   They were not wrong.

P001, P002 and P005 also append a disclaimer footer.

## Reference numbers

Every portal invents its own. We have seen `NPAS-2024-0001234`,
`SPC/2024-25/004512`, `PWD/2024/00871`, bare `00048213`, `ref-2024-00912`,
`MC/2025/W/00311`, and `TN-000412/2024`. The same tender on three portals
has three unrelated reference numbers. There is no cross-walk table and
we have asked.

## Dates

`dd-mm-yyyy`, `dd/mm/yyyy`, `dd.mm.yyyy`, `yyyy-mm-dd`, `12 Mar 2024`,
`Mar 12, 2024`, `12-Mar-24`. The `published_at` column we store is our own
scrape timestamp normalised to ISO, but the dates *inside the body text*
are whatever the portal wrote.

## Money

The same estimated cost appears as `Rs. 4,50,00,000/-`, `Rs. 450.00 lakh`,
`INR 4.500 Cr`, `45000000`, and `RUPEES 4,50,00,000 ONLY`. The
`estimated_value` column is our parse of it and is usually right.

## Other habits

* A few portals publish everything in **UPPER CASE**.
* At least two truncate long notices -- one at about 1,200 characters, one
  at about 2,500. You get the head of the notice and nothing else.
* Corrigenda are published as **new notices**, not as edits. The title
  usually starts with 'Corrigendum' but not always, and the body repeats
  the original notice with a CORRIGENDUM section bolted on the end and a
  new closing date.
* Nodal agencies re-publish a notice days or weeks after the origin portal,
  so `published_at` ordering does not tell you which copy came first.

## Volume, top 15 portals

| portal | notices |
|---|---:|
| `P094` | 1,426 |
| `P002` | 800 |
| `P006` | 792 |
| `P001` | 778 |
| `P003` | 772 |
| `P005` | 768 |
| `P004` | 755 |
| `P020` | 384 |
| `P240` | 377 |
| `P044` | 224 |
| `P215` | 153 |
| `P181` | 133 |
| `P013` | 110 |
| `P227` | 107 |
| `P088` | 97 |

Total: 12,000 notices across 260 portals.