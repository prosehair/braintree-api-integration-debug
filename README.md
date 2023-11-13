# Braintree debug repository

This repository contains the debug tests for the Braintree integration.

## How to run the tests

Install the dependencies:

```bash
make venv
```

Run the tests:

```bash
make test
```

Currently two tests are failing waiting for an explanation from Braintree:

```
FAILED prose/test_braintree_lite.py::BraintreeClientTest::test_create_payment_insufficient_funds - AssertionError: PaymentClientError not raised
FAILED prose/test_braintree_lite.py::BraintreeClientTest::test_refund_payment - NotImplementedError: Refund for authorized and submitted for settlement transactions is not supported
```

- test_create_payment_insufficient_funds fails because Braintree returns a successful transaction for `fake-processor-declined-visa-nonce` Nonce.
- test_refund_payment fails because Braintree does not support automated tests for refunds.
