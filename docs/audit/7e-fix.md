# 7e-fix: Indian mobile redaction and timeout charging (QA notes on #49)

Branch `fix/llm-redaction-timeout`, stacked on #49.

## What changed (`llm.py`)

* n1: phone redaction also covers Indian mobiles without a country code: a
  5-5 grouping (`98765 43210`) and a bare 10-digit run starting with 6-9
  (`9876543210`). `+91 98765 43210` was already covered. Ten-digit runs that
  cannot be a mobile (starting 0-5) and 5-5 groups starting 0-5 stay intact.
* n2: a provider timeout keeps the reserved estimate booked against the caps,
  because the provider may still bill the call. `LlmError` and `OSError`
  (request not completed) still free the reservation.

## Five priorities

* **Latency:** unchanged; one extra regex alternative.
* **Data structures:** none changed.
* **Security:** more contact details are removed before any prompt leaves the
  process.
* **UX:** years, grades and other numbers are still kept, so rewrites stay
  faithful.
* **Cost:** a timed-out call can no longer let usage exceed the daily cap.

## Tests

`tests/test_llm.py`:

* three Indian mobile formats are removed;
* three non-mobile number lines are kept;
* the timeout test now expects the estimate to stay booked.
