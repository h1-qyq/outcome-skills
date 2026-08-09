# Deployment guide

This repository is safe by default: demo mode moves no money, and production
application construction fails when payment, model, or protected-payload
requirements are missing. It is library-shaped and does not ship a precomposed
production ASGI entry point. An operator must supply that composition and an
ASGI server after completing the checks below.

For future payment-specific work, read
[Activate live payments](activate-live-payments.md). Payment activation is not
part of the free public test release.

## 1. Verify the source checkout

Use Python 3.11 or newer in an isolated environment:

```text
python -m pip install --upgrade pip
python -m pip install -e . pytest
python -m pytest -q
python scripts/validate_repo.py
git diff --check
```

Do not continue from a tree that fails a gate or contains generated payment
records, databases, credentials, private keys, or local ledgers.

## 2. Choose an environment deliberately

| Mode | Required configuration | Intended use |
| --- | --- | --- |
| Public test demo | `GATEWAY_ENVIRONMENT=development` or `test`; `PAYMENT_ADAPTER=demo` | Free deterministic local tests; no money moves. |
| Future payment adapter | Explicitly authorized non-production integration | Not enabled in this release. |
| Production | `GATEWAY_ENVIRONMENT=production`; `PAYMENT_ADAPTER=live`; all injected activation evidence and protected services below | Only after every go-live check passes. |

`load_config()` reads a mapping supplied by the composition root. The project
does not automatically load `.env` files. Treat `.env.example` as a list of
names and visibly invalid examples, not as an activation script.

## 3. Keep configuration in the right boundary

## Client gateway URL and local demo

Clients require `OUTCOMES_GATEWAY_URL`. Set it to the operator's exact deployed
HTTPS origin, for example `https://<operator-provided-gateway-origin>`. They
reject remote HTTP, non-HTTP schemes, userinfo, query/fragment URLs, and all
redirects before a payment proof can be forwarded. Only an explicit numeric
loopback HTTP address is accepted for the no-money demo.

```text
python scripts/run_demo_gateway.py --port 8000
OUTCOMES_GATEWAY_URL=http://127.0.0.1:8000
```

The demo binds loopback only, uses ephemeral storage outside the repository,
and grants free public-test access solely for a no-money quote-to-result check.
Production remains an operator-composed ASGI deployment; the repository does
not claim a wallet, JD endpoint, or hosting provider.

The deployment secret manager may supply these runtime values to the process:

- `X402_PAY_TO` and an HTTPS `X402_RESOURCE_BASE_URL`;
- Keep all `CLAWTIP_*` values unset. CNY/JD collection is intentionally
  deferred in this release;
- `MODEL_BASE_URL`, `MODEL_API_KEY`, and `MODEL_NAME` for the production result
  engine.

Do not place real values in repository files, command history, CI variables
that print to logs, issue reports, or public responses. Never log a parsed
configuration object containing secret values.

Two production decisions cannot be represented by strings alone:

1. The USD adapter needs an `AuthenticatedCdpProvider` object whose provider is
   constructed from current official CDP guidance and whose
   `integration_tested` flag is set only after an authenticated integration
   test passes.
2. The deferred CNY/JD rail has no activation path in this release. Do not
   manufacture a wallet, merchant credential, or sandbox attestation.

Environment variable names resembling CDP credentials do not satisfy the first
requirement. JD-issued values are intentionally out of scope until a future
release.

## 4. Supply a protected buyer-payload store

`OrderStore` retains the order ledger and only a hash of normalized buyer
input. Raw input is held behind the separate `BuyerPayloadStore` interface.
The bundled `SQLiteBuyerPayloadStore` is a local fixture with
`production_ready == False`; `create_app(..., environment="production")`
rejects it.

A production implementation must:

- encrypt payloads at rest and in transit;
- enforce least-privilege access and tenant/order isolation;
- bind stored input to `Order.input_hash` and verify it again on read;
- define retention and deletion appropriate to the deployment's legal duties;
- avoid including raw input in application, proxy, tracing, or error logs; and
- return `production_ready == True` only after those controls are tested.

Review the SQLite order ledger separately for the deployment's concurrency,
backup, recovery, and availability needs. Do not place its database in a public
web root or repository checkout.

## 5. Compose the application

The operator-owned composition root must construct these components in order:

```text
GatewayConfig
    + durable OrderStore
    + build_payment_adapter(..., cdp_auth_provider=..., clawtip_activation=...)
    + production-ready BuyerPayloadStore
    -> create_app(..., environment=config.environment)
    -> operator-selected ASGI server
```

In production, `create_app` constructs the configured OpenAI-compatible result
engine from `MODEL_BASE_URL`, `MODEL_API_KEY`, and `MODEL_NAME`. Missing model
configuration, a demo adapter, a non-production-ready payment router, an
unprotected payload store, or a SQLite payload fixture stops startup.

The current `build_payment_adapter` keeps payment interfaces for future work,
but this product release does not expose collection. Do not bypass the
readiness check or inject placeholder wallet values.

## 6. Harden the service edge

- Terminate TLS and set `X402_RESOURCE_BASE_URL` to the exact public HTTPS
  origin used by clients.
- Authenticate administrative and operational interfaces; the repository only
  defines the order API.
- Apply request-size, concurrency, and rate limits before buyer input reaches
  the application.
- Keep model and payment-provider egress allowlisted where the platform permits.
- Redact `Authorization`, `PAYMENT-SIGNATURE`, `Payment-Proof`,
  `PAYMENT-RESPONSE`, and buyer input from logs and traces.
- Monitor state counts and typed failures, not raw proofs or buyer content.

## 7. Run fail-closed verification

In an isolated, authorized environment, verify all of the following before
opening traffic:

1. A new free-test order includes an order ID, private access token, and expiry.
2. Public order responses omit buyer input, input hashes, raw payment data, and
   secret configuration.
3. A result request without valid settlement remains locked.
4. Invalid, mismatched, expired, replayed, and processing payment evidence
   never generates an artifact.
5. A retry uses the same order and proof; it does not create a duplicate charge
   or generate twice.
6. A free-test order can retry a transient model failure without a payment.
7. Future payment adapters remain disabled; CNY/JD remains deferred.
8. No wallet or facilitator is reachable from the public-test composition.
9. A log and trace review finds no secrets, raw proofs, or buyer input.

## 8. Release and rollback

Build an immutable artifact only from reviewed tracked files. Keep generated
databases, order records, caches, and local environment files outside it.
Record the source revision and dependency resolution used for the artifact.

Rollback must stop new order creation first, preserve already-settled order
state, and retain the ability to reconcile a processing settlement with the
same proof. Never discard the ledger merely to make a retry appear clean.

There is no deployment automation in this repository. CI validates source and
does not ship or release it.
