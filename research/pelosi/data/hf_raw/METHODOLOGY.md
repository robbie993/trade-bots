# Methodology

This dataset is built from the official U.S. House and Senate periodic transaction reports by the
open-source [congressional-disclosures](https://github.com/austin-starks/congressional-disclosures) package. This page describes
how rows are produced and lists every filing whose rows were read or corrected by hand.

## Sources

House filings come from the Clerk's annual financial disclosure index and each periodic transaction
report PDF. Senate filings come from eFD: its electronic reports, and the page images it serves for paper
filings. Every filing and trade carries `rawArchiveKey` and `rawSha256`, which identify the exact source
document its rows were read from.

## Extraction

- Senate electronic reports are parsed directly from the report's HTML table.
- House PDFs with a text layer are read from that text by a model bound to a fixed row schema. A filing is
  published when two reads agree, and fails when no two do.
- Scanned House PDFs and Senate paper filings have each page turned upright and OCR'd at two resolutions,
  then read twice: once from the OCR text and once from the page image alone. Rows the two reads disagree on,
  and dates that cannot all be true (a trade dated after the report was filed, or long before its
  notification), go to a reconciling read of the page. A filing fails when the page-image read lists more
  transactions on a page than the OCR captured.
- `extractionModel` and `contractVersion` on each filing name the model and instruction version that
  produced its rows.

## Failures

A filing that cannot be extracted with confidence appears in `political_filings` with
`extractionStatus = 'failed'` and a `failureReason`, and contributes no trades.

## Filings read or corrected by hand

A few filings could not be extracted completely by any automated read, or were published with rows the
filed page contradicts. Those were read or corrected by hand against the filed page images. Each hand reading
is bound to the SHA-256 of the exact PDF it was checked against, so a re-filed document goes back through the
automated pipeline, and its rows pass the same validation as model rows. They are marked in the data:

- `political_filings.extractionModel` is `manual-transcription` for a filing read entirely by
  hand, and ends in `+manual-correction` for a model extraction with rows corrected or added by hand.
- Each corrected or added trade says what was done in `political_trades.comment`.

Some of these are 1-bit, 200-dpi fax scans whose date digits cannot be made out even at native resolution.
Rows on them keep a date only where the page prints one legibly, and their day-of-month values are best
treated as approximate.

### senate:3382ba90-85ad-444b-8685-d09c71e787d9 · John Boozman · filed 2016-08-11

116 trades · `manual-transcription`

Read entirely by hand from the filed page images: 116 transactions. Automated reads could not reconcile their readings of this filing.

### house:8216835 · Gus M. Bilirakis · filed 2020-01-02

2 trades · `manual-transcription`

Read entirely by hand from page 1 of the filed PDF, an amendment of the 6-25-18 report restating two sales by a dependent child. Automated reads could not separate its transactions from the attached broker confirmations.

### house:8216921 · Kenny Marchant · filed 2020-01-22

25 trades · `meta/muse-spark-1.3-contributor+manual-correction`

The extracted rows were checked by hand against the five filed pages, and page 2's Ford purchase was added. The filer dated that purchase 12-4-20, after the report was filed on 1/22/20, beside a notification date of 1-13-20, so it is published as 2019-12-04.

### house:8218338 · Ro Khanna · filed 2021-09-15

1,308 trades · `meta/muse-spark-1.3-contributor+manual-correction`

A 23-page, 200-dpi fax scan. 121 trades that automated extraction missed were added by hand from the page images: all 70 on page 21, 25 on page 12 and 26 on page 18. Pages 18 and 21 print 08/17/21, notified 09/03/21; the dates on page 12 are illegible and left blank. Five trades on page 9 extracted with September dates are corrected to the August dates the page prints, and four asset names are corrected to what the pages print: Fiserv, The Bank of NY Mellon, Align Technology and Hologic. The other rows are published as extracted, and their day-of-month values may be misread.

### senate:ec4d0d90-b4a7-4fb8-a1ac-bbcfd1c3ad6a · Richard Blumenthal · filed 2021-12-13

2 trades · `manual-transcription`

Read entirely by hand from the filed page images: 2 transactions. Automated reads could not reconcile their readings of this filing.

### house:8218637 · Ro Khanna · filed 2022-04-07

387 trades · `meta/muse-spark-1.3-contributor+manual-correction`

A 200-dpi fax scan. 310 notification dates extracted as falling after the 4/7/22 filing are corrected to 2022-04-05, since the pages print 04/0?/22 with a last digit that reads as 5 or 9. The other rows are published as extracted, and their day-of-month values may be misread.

### senate:7f733a14-a6be-4cc1-af27-c4f059fedb3f · Richard Blumenthal · filed 2022-09-07

31 trades · `manual-transcription`

Read entirely by hand from the filed page images: 31 transactions. Automated reads could not reconcile their readings of this filing.

### senate:d7d35e4a-545e-4215-a318-f90031461bc3 · Richard Blumenthal · filed 2025-01-21

3 trades · `manual-transcription`

Read entirely by hand from the filed page images: 3 transactions. Automated reads could not reconcile their readings of this filing.

### senate:14607985-10ba-44a3-bd64-9840e003b06e · Richard Blumenthal · filed 2025-02-10

24 trades · `manual-transcription`

Read entirely by hand from the filed page images: 24 transactions. Automated reads could not reconcile their readings of this filing.
