# Payment boundary

The public release is free during its test period. The loopback demo grants
access without payment, wallets, merchant accounts, or money movement. Each
order still receives a private result-access token so result delivery is not a
public unauthenticated endpoint.

The repository retains payment-adapter interfaces as dormant future seams. No
USD facilitator, receiver, JD merchant, ClawTip wallet, or payment credential
is configured or claimed in this release. Do not enable those adapters during
the public test.

## Future payment work

Any later payment release must be separately authorized, specify its receiver
and facilitator, rerun the full integration and security checks, and update the
public documentation. CNY/JD remains explicitly deferred; leave all
`CLAWTIP_*` values unset.
