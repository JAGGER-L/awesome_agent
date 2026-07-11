# ADR 0002: SQLite and disposable development state

- Status: Accepted and implemented
- Date: 2026-07-10

## Decision

V1 uses ordinary files plus two embedded SQLite databases under
`AWESOME_HOME`: Application owns bounded product records in `application.db`;
LangGraph owns graph checkpoints in `checkpoints.db`. Workspace files are the
primary user-visible state.

All state created before the Local-first cutover is disposable. There is no
importer, dual read/write period, compatibility adapter, or preservation of old
test conversations and checkpoints.

## Consequences

Installation needs no database service. Tests create isolated fresh state.
V1 schemas become the starting point for future forward schema evolution.

## Rejected

A service database harms local setup and adds operations work. JSON for all
state lacks transactions and indexes needed for Thread/Turn and change
lifecycle. Migrating valueless development records would preserve complexity,
not user value.
