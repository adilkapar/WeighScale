*&---------------------------------------------------------------------*
*& Class ZCL_WEIGH_SCALE_CLIENT
*& Weighbridge REST API Client for SAP ABAP
*&---------------------------------------------------------------------*
*& Calls a Python-based weighbridge REST service to retrieve the
*& current weight reading.  Supports two HTTP client creation modes:
*&   (a) create_by_url  — direct URL, quick for dev/test
*&   (b) create_by_destination — SM59 RFC destination (type G), preferred
*&       for production because connection params are maintained centrally.
*&
*& JSON parsing uses /UI2/CL_JSON (available in most NW 7.40+ systems).
*& A fallback note is included for systems without /UI2/CL_JSON.
*&
*& Author : WeighScale Integration Team
*& License: MIT
*&---------------------------------------------------------------------*

*----------------------------------------------------------------------*
*  Custom exception class for weigh-scale errors (static check)
*----------------------------------------------------------------------*
CLASS zcx_weigh_scale_error DEFINITION
  INHERITING FROM cx_static_check
  PUBLIC
  CREATE PUBLIC.

  PUBLIC SECTION.
    INTERFACES if_t100_message.

    DATA mv_text TYPE string READ-ONLY.

    METHODS constructor
      IMPORTING
        iv_text    TYPE string     OPTIONAL
        !previous  TYPE REF TO cx_root OPTIONAL.

ENDCLASS.

CLASS zcx_weigh_scale_error IMPLEMENTATION.
  METHOD constructor.
    super->constructor( previous = previous ).
    mv_text = iv_text.
    " Use a generic message class for T100 compatibility
    " In a real system you would create ZCX_WEIGH_SCALE_ERROR via SE80
    " with its own message class and message numbers.
  ENDMETHOD.
ENDCLASS.

*----------------------------------------------------------------------*
*  Weight reading structure — maps to the JSON contract
*----------------------------------------------------------------------*
*  JSON keys:  weight (float), unit (string), stable (bool), raw (string)
*----------------------------------------------------------------------*
TYPES: BEGIN OF ty_weight_reading,
         weight TYPE f,            " Decimal weight value
         unit   TYPE string,       " Unit of measurement (kg, lb …)
         stable TYPE abap_bool,    " X = stable, space = unstable
         raw    TYPE string,       " Original ASCII line from the scale
       END OF ty_weight_reading.

*----------------------------------------------------------------------*
*  Main client class
*----------------------------------------------------------------------*
CLASS zcl_weigh_scale_client DEFINITION
  PUBLIC
  CREATE PUBLIC.

  PUBLIC SECTION.

    "! Constructor
    "! @parameter iv_service_url | Full base URL, e.g. http://10.0.0.50:8080
    "!   Used when creating the HTTP client via cl_http_client=>create_by_url.
    "! @parameter iv_rf_dest | SM59 RFC destination name (type G).
    "!   Used with cl_http_client=>create_by_destination.  Takes precedence
    "!   over iv_service_url if both are supplied.
    "! @parameter iv_api_key | Optional API key sent as X-API-Key header.
    METHODS constructor
      IMPORTING
        iv_service_url TYPE string   OPTIONAL
        iv_rf_dest     TYPE rfcdest  OPTIONAL
        iv_api_key     TYPE string   OPTIONAL
      RAISING
        zcx_weigh_scale_error.

    "! Retrieve the current weight reading from the service.
    "! @parameter rs_reading | Typed weight structure
    "! @raising zcx_weigh_scale_error | On any HTTP or parsing error
    METHODS get_weight
      RETURNING VALUE(rs_reading) TYPE ty_weight_reading
      RAISING   zcx_weigh_scale_error.

  PRIVATE SECTION.
    DATA mv_service_url TYPE string.
    DATA mv_rf_dest     TYPE rfcdest.
    DATA mv_api_key     TYPE string.
    DATA mv_use_dest    TYPE abap_bool.    " X = use destination, else URL

    "! Execute a GET request against a path and return the response body.
    METHODS _http_get
      IMPORTING iv_path          TYPE string
      RETURNING VALUE(rv_body)   TYPE string
      RAISING   zcx_weigh_scale_error.

ENDCLASS.

CLASS zcl_weigh_scale_client IMPLEMENTATION.

  METHOD constructor.
    " At least one connection parameter is required.
    IF iv_service_url IS INITIAL AND iv_rf_dest IS INITIAL.
      RAISE EXCEPTION TYPE zcx_weigh_scale_error
        EXPORTING iv_text = |Supply either iv_service_url or iv_rf_dest|.
    ENDIF.

    mv_service_url = iv_service_url.
    mv_rf_dest     = iv_rf_dest.
    mv_api_key     = iv_api_key.

    " Prefer the RFC destination when supplied.
    IF iv_rf_dest IS NOT INITIAL.
      mv_use_dest = abap_true.
    ENDIF.
  ENDMETHOD.


  METHOD get_weight.
    " --- 1. Call the /weight endpoint ---
    DATA(lv_json) = _http_get( iv_path = '/weight' ).

    " --- 2. Parse the JSON response into our structure ---
    " /UI2/CL_JSON is available in NW 7.40 SP02+ and S/4HANA.
    " It handles camelCase ↔ ABAP-name mapping automatically when
    " pretty_name = /ui2/cl_json=>pretty_mode-camel_case.
    "
    " ══════════════════════════════════════════════════════════════
    "  FALLBACK NOTE — if /UI2/CL_JSON is not available:
    "  Replace the DESERIALIZE call with manual parsing using
    "  FIND / SPLIT / REPLACE, or install the open-source ABAP
    "  JSON library "ajson" from https://github.com/sbcgua/ajson.
    " ══════════════════════════════════════════════════════════════

    /ui2/cl_json=>deserialize(
      EXPORTING
        json         = lv_json
        pretty_name  = /ui2/cl_json=>pretty_mode-camel_case
      CHANGING
        data         = rs_reading
    ).

    " --- 3. Quick validation ---
    IF rs_reading-unit IS INITIAL.
      " The parse may have succeeded structurally but returned blanks
      " if the JSON keys didn't match.  Treat this as an error.
      RAISE EXCEPTION TYPE zcx_weigh_scale_error
        EXPORTING iv_text = |JSON parsing returned empty unit — check field mapping. Raw: { lv_json }|.
    ENDIF.

    " ═══════════════════════════════════════════════════════════════
    "  EXTENSION POINT — business logic after a successful reading
    " ═══════════════════════════════════════════════════════════════
    "  At this point rs_reading is populated.  Typical next steps:
    "
    "  • Log to a custom Z-table (e.g. ZWEIGH_LOG) for audit:
    "      INSERT zweigh_log FROM @( VALUE #(
    "        mandt    = sy-mandt
    "        weigh_id = cl_system_uuid=>create_uuid_x16_static( )
    "        weight   = rs_reading-weight
    "        unit     = rs_reading-unit
    "        stable   = rs_reading-stable
    "        raw_line = rs_reading-raw
    "        timestamp = sy-datum && sy-uzeit
    "      ) ).
    "
    "  • Update an inbound delivery (VL32N):
    "      CALL FUNCTION 'WS_DELIVERY_UPDATE' …
    "
    "  • Post to a material document (MIGO / BAPI_GOODSMVT_CREATE):
    "      Populate the gross weight field in the BAPI structure.
    "
    "  • Feed an SD billing document or a custom Fiori app.
    "
    "  RECOMMENDATION: Only act on readings where stable = abap_true.
    "  Unstable readings are transitional and should NOT be posted
    "  to business documents.
    " ═══════════════════════════════════════════════════════════════

  ENDMETHOD.


  METHOD _http_get.
    DATA: lo_client TYPE REF TO if_http_client,
          lv_code   TYPE i,
          lv_reason TYPE string.

    " ─── Create the HTTP client ──────────────────────────────────
    IF mv_use_dest = abap_true.
      "  OPTION (b): SM59 destination — PREFERRED for production.
      "  Create an RFC destination in SM59 of type G (HTTP to external).
      "  Set the target host/port there; path is appended below.
      "  Benefits: connection parameters maintained centrally, load
      "  balancing, SSL/proxy settings in one place.
      cl_http_client=>create_by_destination(
        EXPORTING  destination = mv_rf_dest
        IMPORTING  client      = lo_client
        EXCEPTIONS OTHERS      = 1 ).
      IF sy-subrc <> 0.
        RAISE EXCEPTION TYPE zcx_weigh_scale_error
          EXPORTING iv_text = |create_by_destination failed for { mv_rf_dest } (sy-subrc={ sy-subrc })|.
      ENDIF.
    ELSE.
      "  OPTION (a): Direct URL — convenient for dev/test.
      "  The full URL (scheme + host + port) is passed here.
      "  NOTE on HTTPS: if you use https://, you must import the
      "  server's SSL certificate into STRUST (transaction STRUST,
      "  SSL Client identity ANONYMOUS or a named PSE).
      "  Also ensure ICM parameter icm/HTTPS/client_sni_enabled = TRUE
      "  if SNI is required.
      cl_http_client=>create_by_url(
        EXPORTING  url    = mv_service_url
        IMPORTING  client = lo_client
        EXCEPTIONS OTHERS = 1 ).
      IF sy-subrc <> 0.
        RAISE EXCEPTION TYPE zcx_weigh_scale_error
          EXPORTING iv_text = |create_by_url failed for { mv_service_url } (sy-subrc={ sy-subrc })|.
      ENDIF.
    ENDIF.

    " ─── Set request details ─────────────────────────────────────
    lo_client->request->set_method( if_http_request=>co_request_method_get ).

    " Append the path (e.g. /weight or /health)
    cl_http_utility=>set_request_uri(
      request = lo_client->request
      uri     = iv_path ).

    " Optional API-key header
    IF mv_api_key IS NOT INITIAL.
      lo_client->request->set_header_field(
        name  = 'X-API-Key'
        value = mv_api_key ).
    ENDIF.

    " Accept JSON
    lo_client->request->set_header_field(
      name  = 'Accept'
      value = 'application/json' ).

    " ─── Send & receive ──────────────────────────────────────────
    lo_client->send(
      EXCEPTIONS OTHERS = 1 ).
    IF sy-subrc <> 0.
      lo_client->close( ).
      RAISE EXCEPTION TYPE zcx_weigh_scale_error
        EXPORTING iv_text = |HTTP send failed (sy-subrc={ sy-subrc })|.
    ENDIF.

    lo_client->receive(
      EXCEPTIONS OTHERS = 1 ).
    IF sy-subrc <> 0.
      lo_client->close( ).
      RAISE EXCEPTION TYPE zcx_weigh_scale_error
        EXPORTING iv_text = |HTTP receive failed (sy-subrc={ sy-subrc })|.
    ENDIF.

    " ─── Check HTTP status ───────────────────────────────────────
    lo_client->response->get_status(
      IMPORTING code   = lv_code
                reason = lv_reason ).

    IF lv_code <> 200.
      DATA(lv_body) = lo_client->response->get_cdata( ).
      lo_client->close( ).
      RAISE EXCEPTION TYPE zcx_weigh_scale_error
        EXPORTING iv_text = |HTTP { lv_code } { lv_reason }: { lv_body }|.
    ENDIF.

    " ─── Return JSON body ────────────────────────────────────────
    rv_body = lo_client->response->get_cdata( ).
    lo_client->close( ).
  ENDMETHOD.

ENDCLASS.
