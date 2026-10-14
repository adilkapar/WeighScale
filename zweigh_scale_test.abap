*&---------------------------------------------------------------------*
*& Report ZWEIGH_SCALE_TEST
*& Test program for the Weighbridge REST API Client
*&---------------------------------------------------------------------*
*& Instantiates ZCL_WEIGH_SCALE_CLIENT, calls GET /weight, and
*& displays the result.  Use this to verify end-to-end connectivity
*& from SAP to the Python scale service (real or mock).
*&
*& How to run
*& ----------
*& 1. Start one of the Python services:
*&      python scale_service_mock.py          (Mode 1 — no hardware)
*&      python scale_service.py               (Mode 2/3 — serial)
*&
*& 2. Adjust p_url or p_dest below and execute this report (F8/SA38).
*&
*& Author : WeighScale Integration Team
*& License: MIT
*&---------------------------------------------------------------------*

REPORT zweigh_scale_test.

*----------------------------------------------------------------------*
*  Selection screen
*----------------------------------------------------------------------*
PARAMETERS:
  " ── Connection mode ──────────────────────────────────────────────
  p_url    TYPE string   DEFAULT 'http://localhost:8080'
           LOWER CASE
           OBLIGATORY,             " Direct URL (option a)

  p_dest   TYPE rfcdest
           LOWER CASE,             " SM59 destination (option b) — leave blank to use URL

  " ── Optional API key ─────────────────────────────────────────────
  p_apikey TYPE string
           LOWER CASE.             " Must match the Python service's API_KEY


*----------------------------------------------------------------------*
*  Main logic
*----------------------------------------------------------------------*
START-OF-SELECTION.

  DATA: lo_client  TYPE REF TO zcl_weigh_scale_client,
        ls_reading TYPE ty_weight_reading.

  TRY.
      " ── 1. Create the client ──────────────────────────────────
      IF p_dest IS NOT INITIAL.
        " Prefer SM59 destination when provided
        CREATE OBJECT lo_client
          EXPORTING
            iv_rf_dest = p_dest
            iv_api_key = p_apikey.
      ELSE.
        CREATE OBJECT lo_client
          EXPORTING
            iv_service_url = p_url
            iv_api_key     = p_apikey.
      ENDIF.

      " ── 2. Retrieve the current weight ────────────────────────
      ls_reading = lo_client->get_weight( ).

      " ── 3. Display the result ─────────────────────────────────
      WRITE: / '═══════════════════════════════════════════'.
      WRITE: / ' Weighbridge Reading'.
      WRITE: / '═══════════════════════════════════════════'.
      WRITE: / ' Weight :', ls_reading-weight, ls_reading-unit.
      WRITE: / ' Stable :', ls_reading-stable.
      WRITE: / ' Raw    :', ls_reading-raw.
      WRITE: / '═══════════════════════════════════════════'.

      IF ls_reading-stable = abap_true.
        WRITE: / ' ✓ Reading is STABLE — safe for business posting.'.
      ELSE.
        WRITE: / ' ⚠ Reading is UNSTABLE — do NOT post to documents.'.
      ENDIF.

      " ═══════════════════════════════════════════════════════════
      "  EXTENSION POINT — after a successful read you could:
      "  • Write to a Z-table log
      "  • Call a BAPI (e.g. BAPI_GOODSMVT_CREATE for MIGO)
      "  • Trigger a workflow event
      "  • Update an inbound delivery
      "  See zcl_weigh_scale_client->get_weight() for details.
      " ═══════════════════════════════════════════════════════════

    CATCH zcx_weigh_scale_error INTO DATA(lx_error).
      " ── Error handling ────────────────────────────────────────
      WRITE: / '╔═══════════════════════════════════════════╗'.
      WRITE: / '║  ERROR retrieving weight                  ║'.
      WRITE: / '╚═══════════════════════════════════════════╝'.
      WRITE: / ' Message:', lx_error->mv_text.

      " Diagnostic tips
      WRITE: / ''.
      WRITE: / ' Troubleshooting:'.
      WRITE: / ' 1. Is the Python service running?'.
      WRITE: / '    curl', p_url, '/health'.
      WRITE: / ' 2. Can this SAP system reach the host?'.
      WRITE: / '    Check firewall, proxy, and ICM settings.'.
      WRITE: / ' 3. If using SM59, verify the destination in SM59.'.
      WRITE: / ' 4. If HTTPS, import the cert via STRUST.'.
  ENDTRY.
