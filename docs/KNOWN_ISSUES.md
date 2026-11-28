# Known issues

## Lake writer: `hash_mismatch` case lands a valid message

`tests/unit/pipeline/test_lake_writer_and_publisher.py::test_permanent_failures_go_to_the_dlq_and_are_committed[<lambda>-hash_mismatch]` fails on `main` (`55a91b7`) and on `league-week1`. The rest of that file passes.

The case expects `DEAD_LETTERED` / `hash_mismatch`. It tries to tamper the published market message by replacing the bytes `"volume":10000` with `"volume":10001`. The writer instead returns `LANDED` with no failure reason.

The published JSON does not contain those bytes. Volume is a six-decimal `Decimal`, and the wire value is `"volume":"10000.000000"`. The replace matches nothing, so the message is unchanged and the hash still agrees with the record.

Not a League bug. Fix it on its own branch.
