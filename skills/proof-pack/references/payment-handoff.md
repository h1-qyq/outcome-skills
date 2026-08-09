# Payment handoff

The gateway chooses the provider from the server-owned currency. Do not choose a provider, amount, receiver, or order identifier for the buyer.

- USD/x402: after explicit authorization for the displayed order, submit the signed value as `PAYMENT-SIGNATURE` to the gateway. The gateway returns the v2 `PAYMENT-REQUIRED` payload to sign and only generates after settlement succeeds. Never expose keys, signatures, or facilitator credentials.
- CNY/JD: deferred. Do not create a payment URL, invoke ClawTip, request a wallet, or collect a credential. The gateway must fail closed for CNY until a future release explicitly reopens this rail.

`payCredential`, wallet keys, SM4 keys, and CDP credentials are secret. Do not print, retain, or repeat them.
