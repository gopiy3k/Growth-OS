name: Pull Request

description: Contribute a change to Growth OS
title: "<type>: <short summary>"
labels: []
body:
  - type: checkboxes
    id: checks
    attributes:
      label: Before you merge
      options:
        - label: "Contract tests pass (`pytest tests/contract`)"
        - label: "No references to any external private product, brand, or infrastructure"
        - label: "No secrets committed (`.env.example` only)"
        - label: "Migration lint clean (no product-table references)"
        - label: "Architectural change references an ADR (or adds one)"
        - label: "Conventional Commits used"
    validations:
      required: true
  - type: textarea
    id: what
    attributes:
      label: What & why
    validations:
      required: true
  - type: textarea
    id: adr
    attributes:
      label: ADR reference (if architectural)
    validations:
      required: false
