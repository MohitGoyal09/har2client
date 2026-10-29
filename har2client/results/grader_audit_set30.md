# Grader-validity audit

## Oracle

30 tasks; reference client scores 1.0 on 30.

## Trivial agents

| agent | tasks | mean pass_rate | max | tasks > 0 | hardcoded_secret flagged | hardcoded_host flagged |
|---|---|---|---|---|---|---|
| empty_client | 30 | 0.000 | 0.0 | - | 0 | 0 |
| har_replay_client | 30 | 0.000 | 0.0 | - | 30 | 30 |

## Kill rate

Overall: 392/501 = 78.2% (excluding EQUIVALENT survivors: 92.9%). Skipped (operator not applicable): 249. Errors: 0.
Survivor classes: {'FLAG_ONLY': 30, 'EQUIVALENT': 79}

| operator | graded | killed | survived | kill rate | skipped | survivor classes |
|---|---|---|---|---|---|---|
| auth_header_name_wrong | 19 | 19 | 0 | 100.0% | 11 | - |
| auth_in_header | 3 | 3 | 0 | 100.0% | 27 | - |
| auth_in_query | 19 | 19 | 0 | 100.0% | 11 | - |
| base_path_doubled | 12 | 12 | 0 | 100.0% | 18 | - |
| base_path_dropped | 12 | 12 | 0 | 100.0% | 18 | - |
| basic_prefix_missing | 7 | 7 | 0 | 100.0% | 23 | - |
| bearer_prefix_missing | 8 | 8 | 0 | 100.0% | 22 | - |
| body_as_form | 19 | 19 | 0 | 100.0% | 11 | - |
| body_dropped | 19 | 19 | 0 | 100.0% | 11 | - |
| bool_encoding_numeric | 6 | 6 | 0 | 100.0% | 24 | - |
| bool_encoding_python_repr | 6 | 6 | 0 | 100.0% | 24 | - |
| credential_also_in_query | 19 | 0 | 19 | 0.0% | 11 | {'EQUIVALENT': 19} |
| credential_hardcoded | 22 | 22 | 0 | 100.0% | 8 | - |
| empty_body_object_when_none | 30 | 0 | 30 | 0.0% | 0 | {'EQUIVALENT': 30} |
| error_swallowed | 30 | 0 | 30 | 0.0% | 0 | {'EQUIVALENT': 30} |
| extra_query_param | 30 | 30 | 0 | 100.0% | 0 | - |
| extra_unrequested_call | 30 | 0 | 30 | 0.0% | 0 | {'FLAG_ONLY': 30} |
| host_hardcoded | 30 | 30 | 0 | 100.0% | 0 | - |
| path_param_in_query | 25 | 25 | 0 | 100.0% | 5 | - |
| path_segment_changed | 29 | 29 | 0 | 100.0% | 1 | - |
| query_param_dropped | 20 | 20 | 0 | 100.0% | 10 | - |
| query_param_renamed | 20 | 20 | 0 | 100.0% | 10 | - |
| response_not_parsed | 29 | 29 | 0 | 100.0% | 1 | - |
| returns_field_not_body | 27 | 27 | 0 | 100.0% | 3 | - |
| wrong_http_method | 30 | 30 | 0 | 100.0% | 0 | - |

## Survivors

| task | operator | op | class | reason |
|---|---|---|---|---|
| apiz.ebay.com_sell-finances | extra_unrequested_call |  | FLAG_ONLY | invented_endpoint_calls=14 reported, not scored |
| bbci.co.uk | extra_unrequested_call |  | FLAG_ONLY | invented_endpoint_calls=16 reported, not scored |
| conjur.local | extra_unrequested_call |  | FLAG_ONLY | invented_endpoint_calls=4 reported, not scored |
| digitallocker.gov.in_authpartner | extra_unrequested_call |  | FLAG_ONLY | invented_endpoint_calls=16 reported, not scored |
| elevenlabs.io | extra_unrequested_call |  | FLAG_ONLY | invented_endpoint_calls=16 reported, not scored |
| elmah.io | extra_unrequested_call |  | FLAG_ONLY | invented_endpoint_calls=16 reported, not scored |
| formapi.io | extra_unrequested_call |  | FLAG_ONLY | invented_endpoint_calls=14 reported, not scored |
| goog.io | extra_unrequested_call |  | FLAG_ONLY | invented_endpoint_calls=12 reported, not scored |
| google.com | extra_unrequested_call |  | FLAG_ONLY | invented_endpoint_calls=16 reported, not scored |
| googleapis.com_firebasedatabase | extra_unrequested_call |  | FLAG_ONLY | invented_endpoint_calls=14 reported, not scored |
| googleapis.com_pubsub | extra_unrequested_call |  | FLAG_ONLY | invented_endpoint_calls=8 reported, not scored |
| hubapi.com_crm | extra_unrequested_call |  | FLAG_ONLY | invented_endpoint_calls=10 reported, not scored |
| impala.travel_hotels | extra_unrequested_call |  | FLAG_ONLY | invented_endpoint_calls=12 reported, not scored |
| letmc.com_customer | extra_unrequested_call |  | FLAG_ONLY | invented_endpoint_calls=6 reported, not scored |
| microsoft.com_cognitiveservices-ComputerVision | extra_unrequested_call |  | FLAG_ONLY | invented_endpoint_calls=12 reported, not scored |
| netlicensing.io | extra_unrequested_call |  | FLAG_ONLY | invented_endpoint_calls=4 reported, not scored |
| nexmo.com_application.v2 | extra_unrequested_call |  | FLAG_ONLY | invented_endpoint_calls=8 reported, not scored |
| nfusionsolutions.biz | extra_unrequested_call |  | FLAG_ONLY | invented_endpoint_calls=16 reported, not scored |
| openstates.org | extra_unrequested_call |  | FLAG_ONLY | invented_endpoint_calls=16 reported, not scored |
| ote-godaddy.com_certificates | extra_unrequested_call |  | FLAG_ONLY | invented_endpoint_calls=16 reported, not scored |
| pdfbroker.io | extra_unrequested_call |  | FLAG_ONLY | invented_endpoint_calls=12 reported, not scored |
| pinecone.io | extra_unrequested_call |  | FLAG_ONLY | invented_endpoint_calls=14 reported, not scored |
| redhat.com_catalog_inventory | extra_unrequested_call |  | FLAG_ONLY | invented_endpoint_calls=16 reported, not scored |
| rev.ai | extra_unrequested_call |  | FLAG_ONLY | invented_endpoint_calls=8 reported, not scored |
| shop-pro.jp | extra_unrequested_call |  | FLAG_ONLY | invented_endpoint_calls=16 reported, not scored |
| surevoip.co.uk | extra_unrequested_call |  | FLAG_ONLY | invented_endpoint_calls=10 reported, not scored |
| telematicssdk.com | extra_unrequested_call |  | FLAG_ONLY | invented_endpoint_calls=12 reported, not scored |
| twilio.com_twilio_wireless_v1 | extra_unrequested_call |  | FLAG_ONLY | invented_endpoint_calls=16 reported, not scored |
| up.com.au | extra_unrequested_call |  | FLAG_ONLY | invented_endpoint_calls=16 reported, not scored |
| vonage.com_vgis | extra_unrequested_call |  | FLAG_ONLY | invented_endpoint_calls=16 reported, not scored |
| apiz.ebay.com_sell-finances | credential_also_in_query |  | EQUIVALENT | design_leniency_1 |
| conjur.local | credential_also_in_query |  | EQUIVALENT | design_leniency_1 |
| digitallocker.gov.in_authpartner | credential_also_in_query |  | EQUIVALENT | design_leniency_1 |
| formapi.io | credential_also_in_query |  | EQUIVALENT | design_leniency_1 |
| goog.io | credential_also_in_query |  | EQUIVALENT | design_leniency_1 |
| googleapis.com_firebasedatabase | credential_also_in_query |  | EQUIVALENT | design_leniency_1 |
| googleapis.com_pubsub | credential_also_in_query |  | EQUIVALENT | design_leniency_1 |
| impala.travel_hotels | credential_also_in_query |  | EQUIVALENT | design_leniency_1 |
| microsoft.com_cognitiveservices-ComputerVision | credential_also_in_query |  | EQUIVALENT | design_leniency_1 |
| netlicensing.io | credential_also_in_query |  | EQUIVALENT | design_leniency_1 |
| nexmo.com_application.v2 | credential_also_in_query |  | EQUIVALENT | design_leniency_1 |
| pdfbroker.io | credential_also_in_query |  | EQUIVALENT | design_leniency_1 |
| pinecone.io | credential_also_in_query |  | EQUIVALENT | design_leniency_1 |
| redhat.com_catalog_inventory | credential_also_in_query |  | EQUIVALENT | design_leniency_1 |
| rev.ai | credential_also_in_query |  | EQUIVALENT | design_leniency_1 |
| shop-pro.jp | credential_also_in_query |  | EQUIVALENT | design_leniency_1 |
| surevoip.co.uk | credential_also_in_query |  | EQUIVALENT | design_leniency_1 |
| twilio.com_twilio_wireless_v1 | credential_also_in_query |  | EQUIVALENT | design_leniency_1 |
| up.com.au | credential_also_in_query |  | EQUIVALENT | design_leniency_1 |
| apiz.ebay.com_sell-finances | empty_body_object_when_none |  | EQUIVALENT | design_leniency_2 |
| bbci.co.uk | empty_body_object_when_none |  | EQUIVALENT | design_leniency_2 |
| conjur.local | empty_body_object_when_none |  | EQUIVALENT | design_leniency_2 |
| digitallocker.gov.in_authpartner | empty_body_object_when_none |  | EQUIVALENT | design_leniency_2 |
| elevenlabs.io | empty_body_object_when_none |  | EQUIVALENT | design_leniency_2 |
| elmah.io | empty_body_object_when_none |  | EQUIVALENT | design_leniency_2 |
| formapi.io | empty_body_object_when_none |  | EQUIVALENT | design_leniency_2 |
| goog.io | empty_body_object_when_none |  | EQUIVALENT | design_leniency_2 |
| google.com | empty_body_object_when_none |  | EQUIVALENT | design_leniency_2 |
| googleapis.com_firebasedatabase | empty_body_object_when_none |  | EQUIVALENT | design_leniency_2 |
| googleapis.com_pubsub | empty_body_object_when_none |  | EQUIVALENT | design_leniency_2 |
| hubapi.com_crm | empty_body_object_when_none |  | EQUIVALENT | design_leniency_2 |
| impala.travel_hotels | empty_body_object_when_none |  | EQUIVALENT | design_leniency_2 |
| letmc.com_customer | empty_body_object_when_none |  | EQUIVALENT | design_leniency_2 |
| microsoft.com_cognitiveservices-ComputerVision | empty_body_object_when_none |  | EQUIVALENT | design_leniency_2 |
| netlicensing.io | empty_body_object_when_none |  | EQUIVALENT | design_leniency_2 |
| nexmo.com_application.v2 | empty_body_object_when_none |  | EQUIVALENT | design_leniency_2 |
| nfusionsolutions.biz | empty_body_object_when_none |  | EQUIVALENT | design_leniency_2 |
| openstates.org | empty_body_object_when_none |  | EQUIVALENT | design_leniency_2 |
| ote-godaddy.com_certificates | empty_body_object_when_none |  | EQUIVALENT | design_leniency_2 |
| pdfbroker.io | empty_body_object_when_none |  | EQUIVALENT | design_leniency_2 |
| pinecone.io | empty_body_object_when_none |  | EQUIVALENT | design_leniency_2 |
| redhat.com_catalog_inventory | empty_body_object_when_none |  | EQUIVALENT | design_leniency_2 |
| rev.ai | empty_body_object_when_none |  | EQUIVALENT | design_leniency_2 |
| shop-pro.jp | empty_body_object_when_none |  | EQUIVALENT | design_leniency_2 |
| surevoip.co.uk | empty_body_object_when_none |  | EQUIVALENT | design_leniency_2 |
| telematicssdk.com | empty_body_object_when_none |  | EQUIVALENT | design_leniency_2 |
| twilio.com_twilio_wireless_v1 | empty_body_object_when_none |  | EQUIVALENT | design_leniency_2 |
| up.com.au | empty_body_object_when_none |  | EQUIVALENT | design_leniency_2 |
| vonage.com_vgis | empty_body_object_when_none |  | EQUIVALENT | design_leniency_2 |
| apiz.ebay.com_sell-finances | error_swallowed |  | EQUIVALENT | error_path_not_exercised |
| bbci.co.uk | error_swallowed |  | EQUIVALENT | error_path_not_exercised |
| conjur.local | error_swallowed |  | EQUIVALENT | error_path_not_exercised |
| digitallocker.gov.in_authpartner | error_swallowed |  | EQUIVALENT | error_path_not_exercised |
| elevenlabs.io | error_swallowed |  | EQUIVALENT | error_path_not_exercised |
| elmah.io | error_swallowed |  | EQUIVALENT | error_path_not_exercised |
| formapi.io | error_swallowed |  | EQUIVALENT | error_path_not_exercised |
| goog.io | error_swallowed |  | EQUIVALENT | error_path_not_exercised |
| google.com | error_swallowed |  | EQUIVALENT | error_path_not_exercised |
| googleapis.com_firebasedatabase | error_swallowed |  | EQUIVALENT | error_path_not_exercised |
| googleapis.com_pubsub | error_swallowed |  | EQUIVALENT | error_path_not_exercised |
| hubapi.com_crm | error_swallowed |  | EQUIVALENT | error_path_not_exercised |
| impala.travel_hotels | error_swallowed |  | EQUIVALENT | error_path_not_exercised |
| letmc.com_customer | error_swallowed |  | EQUIVALENT | error_path_not_exercised |
| microsoft.com_cognitiveservices-ComputerVision | error_swallowed |  | EQUIVALENT | error_path_not_exercised |
| netlicensing.io | error_swallowed |  | EQUIVALENT | error_path_not_exercised |
| nexmo.com_application.v2 | error_swallowed |  | EQUIVALENT | error_path_not_exercised |
| nfusionsolutions.biz | error_swallowed |  | EQUIVALENT | error_path_not_exercised |
| openstates.org | error_swallowed |  | EQUIVALENT | error_path_not_exercised |
| ote-godaddy.com_certificates | error_swallowed |  | EQUIVALENT | error_path_not_exercised |
| pdfbroker.io | error_swallowed |  | EQUIVALENT | error_path_not_exercised |
| pinecone.io | error_swallowed |  | EQUIVALENT | error_path_not_exercised |
| redhat.com_catalog_inventory | error_swallowed |  | EQUIVALENT | error_path_not_exercised |
| rev.ai | error_swallowed |  | EQUIVALENT | error_path_not_exercised |
| shop-pro.jp | error_swallowed |  | EQUIVALENT | error_path_not_exercised |
| surevoip.co.uk | error_swallowed |  | EQUIVALENT | error_path_not_exercised |
| telematicssdk.com | error_swallowed |  | EQUIVALENT | error_path_not_exercised |
| twilio.com_twilio_wireless_v1 | error_swallowed |  | EQUIVALENT | error_path_not_exercised |
| up.com.au | error_swallowed |  | EQUIVALENT | error_path_not_exercised |
| vonage.com_vgis | error_swallowed |  | EQUIVALENT | error_path_not_exercised |

## Skipped operators

- auth_header_name_wrong: 7x API has no auth (security type none); 3x auth is sent in the query, not a header; 1x no tested operation requires auth
- auth_in_header: 19x auth is not an API key in the query; 7x API has no auth (security type none); 1x no tested operation requires auth
- auth_in_query: 7x API has no auth (security type none); 3x auth is sent in the query, not a header; 1x no tested operation requires auth
- base_path_doubled: 18x spec has no base path
- base_path_dropped: 18x spec has no base path
- basic_prefix_missing: 8x auth is bearer, not basic; 7x API has no auth (security type none); 7x auth is apiKey, not basic; 1x no tested operation requires auth
- bearer_prefix_missing: 7x auth is basic, not bearer; 7x API has no auth (security type none); 7x auth is apiKey, not bearer; 1x no tested operation requires auth
- body_as_form: 11x no hidden test passes a body
- body_dropped: 11x no hidden test passes a body
- bool_encoding_numeric: 24x no hidden test passes a boolean query or path param
- bool_encoding_python_repr: 24x no hidden test passes a boolean query or path param
- credential_also_in_query: 7x API has no auth (security type none); 3x auth is sent in the query, not a header; 1x no tested operation requires auth
- credential_hardcoded: 7x API has no auth (security type none); 1x no tested operation requires auth
- path_param_in_query: 5x no tested operation has a path param
- path_segment_changed: 1x no tested operation has a literal path segment
- query_param_dropped: 10x no hidden test passes a query param
- query_param_renamed: 10x no hidden test passes a query param
- response_not_parsed: 1x no tested operation returns a JSON body
- returns_field_not_body: 3x no tested operation returns a JSON object with fields
