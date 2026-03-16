# Identity Claim Rules

## Purpose

This document defines the national ID claim system used to prevent permanent lock-out when a wrong or fake national ID is entered before the real owner appears.

## Problem

A simple `unique=True` constraint on `User.national_id` is too strict for this platform.

If User A enters a fake or incorrect national ID first, User B, the real owner, becomes permanently blocked from using their own ID unless staff manually intervene.

## Solution

The platform uses `IdentityClaim` as the source of truth for national ID ownership.

A claim is not final when created.

The system allows temporary competing claims while they are still unresolved.

## Statuses

- `UNVERIFIED`: User entered a national ID, but no review was requested yet.
- `UNDER_REVIEW`: Claim was submitted for manual review with optional evidence.
- `VERIFIED`: Platform administration approved this claim. This is the only status that establishes global ownership.
- `REJECTED`: Claim is closed and no longer active.

## Core Rules

- A user entering a national ID does not become the permanent owner immediately.
- Multiple users may temporarily hold the same national ID only while claims are `UNVERIFIED` or `UNDER_REVIEW`.
- Only one `VERIFIED` claim may exist globally for a given national ID.
- When one claim becomes `VERIFIED`, all other active claims for that same national ID must become `REJECTED`.
- Verified medical, appointment, and billing data must continue to attach to the existing `User` and profile structure, not directly to national ID.

## Why Claims Exist

Claims solve the fake-ID-first / real-owner-later problem safely.

Example:

1. User A enters national ID `123456789`.
2. User A remains only `UNVERIFIED` or `UNDER_REVIEW`.
3. User B later enters the same ID and submits proper evidence.
4. Admin verifies User B's claim.
5. User B becomes the only verified owner.
6. User A's active claim is automatically rejected.

## Developer Rules

- Never bypass the identity claim service layer.
- Never attach identity-claim business logic directly in views.
- Never rely on `CustomUser.national_id` for pending-claim checks.
- Use `CustomUser.national_id` only as a backward-compatible shadow of the currently verified claim.
- Never use national ID as the identity key for medical records, appointments, billing, or clinic membership.

## Validation Rules

Before a claim is created, the national ID must be normalized and validated.

Normalization:

- remove spaces
- remove dashes
- remove dots

Validation:

- digits only
- exact length: 9 digits
- reject obvious junk such as repeated digits and simple sequential test values

## Concurrency Rules

Verification must run inside a database transaction.

The verification flow must lock competing rows and rely on a database partial unique constraint so that two concurrent review actions cannot verify the same national ID twice.
