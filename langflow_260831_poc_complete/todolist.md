# Langflow newType TODO List

湲곗?: ?뚯씪 ?섎굹瑜?湲곕낯 媛쒕컻 task ?⑥쐞濡?蹂몃떎.  
?뚯뒪?몃뒗 蹂꾨룄 task濡?遺꾨━?쒕떎.  
1 WD = 媛쒕컻??1 working day.

## 0. 怨듯넻 / Flow

| 援щ텇 | Task | ?뚯씪 | 湲곌컙 |
|---|---|---|---:|
| 媛쒕컻 | newType ?꾩껜 ?몃뱶 ?곌껐 臾몄꽌 理쒖떊??| `01_architecture.md` | 0.5 WD |
| 媛쒕컻 | ?ъ슜???낅젰??`GENERAL_CHAT`, `MANAGEMENT`, `JOB_EXECUTION`?쇰줈 遺꾨쪟?섎뒗 Request Classifier LLM ?꾨＼?꾪듃 ?뺣━ | `01_requestClassifierPrompt.md` | 1.0 WD |
| ?뚯뒪??| Request Classifier LLM ?섑뵆 ?낅젰蹂?JSON route 寃利?| `01_requestClassifierPrompt.md` | 0.5 WD |
| 媛쒕컻 | 1李?route瑜?multi-output?쇰줈 遺꾧린?섎뒗 Conditional Router ?뺣━ | `02_intentRouter.py` | 0.5 WD |
| ?뚯뒪??| 1李?Router?먯꽌 ?좏깮?섏? ?딆? branch媛 ?ㅽ뻾?섏? ?딅뒗吏 寃利?| `02_intentRouter.py` | 0.5 WD |
| 媛쒕컻 | ?쇰컲 ???branch LLM ?묐떟 ?꾨＼?꾪듃 ?뺣━ | `03_llmResponsePrompt.md` | 0.25 WD |
| 媛쒕컻 | 理쒖쥌 Chat Output 硫붿떆吏 ?앹꽦 而댄룷?뚰듃 ?뺣━ | `13_finalSummary.py` | 0.75 WD |
| ?뚯뒪??| Final Summary媛 ?깃났/?ㅽ뙣/?좏뻾?묒뾽李⑤떒/??곸뾾??寃곌낵瑜?紐⑤몢 硫붿떆吏濡?蹂?섑븯?붿? 寃利?| `13_finalSummary.py` | 0.5 WD |

怨듯넻 ?뚭퀎: 4.5 WD

## 1. Management

| 援щ텇 | Task | ?뚯씪 | 湲곌컙 |
|---|---|---|---:|
| 媛쒕컻 | Management ?붿껌??Dashboard, Status Change, Correct SQL Input?쇰줈 遺꾧린?섎뒗 Router ?뺣━ | `04_managementRouter.py` | 1.0 WD |
| ?뚯뒪??| Management Router ?섑뵆 ?낅젰蹂?route 諛?Exception Message 寃利?| `04_managementRouter.py` | 0.5 WD |
| 媛쒕컻 | Dashboard 議고쉶 branch瑜?Langflow 而댄룷?뚰듃 ?뺥깭濡?由ы뙥?좊쭅 | `04_dashboard.py` | 1.0 WD |
| ?뚯뒪??| Dashboard branch DB 議고쉶 寃곌낵 payload/message 寃利?| `04_dashboard.py` | 0.5 WD |
| 媛쒕컻 | Status/priority/USE_YN 蹂寃?branch瑜?Langflow 而댄룷?뚰듃 ?뺥깭濡?由ы뙥?좊쭅 | `04_statusChange.py` | 1.5 WD |
| ?뚯뒪??| priority/status/USE_YN 蹂寃??붿껌 payload 寃利?| `04_statusChange.py` | 0.75 WD |
| 媛쒕컻 | Correct SQL ?낅젰 branch瑜?Langflow 而댄룷?뚰듃 ?뺥깭濡?由ы뙥?좊쭅 | `04_correctSqlInput.py` | 1.5 WD |
| ?뚯뒪??| Correct SQL ?낅젰 ??USER_EDITED 諛?SQL ???payload 寃利?| `04_correctSqlInput.py` | 0.75 WD |

Management ?뚭퀎: 7.5 WD

## 2. Job Target Execution Routing

| 援щ텇 | Task | ?뚯씪 | 湲곌컙 |
|---|---|---|---:|
| 媛쒕컻 | ?꾩껜 ?붿뿬 ?묒뾽 ?꾨낫瑜?議고쉶?댁꽌 domain蹂?context濡?留뚮뱶??而댄룷?뚰듃 ?뺣━ | `06_getRemainingJobs.py` | 1.25 WD |
| ?뚯뒪??| MIG/SQL Conversion/Tuning/Formatting ?꾨낫 議고쉶 寃곌낵 援ъ“ 寃利?| `06_getRemainingJobs.py` | 0.75 WD |
| 媛쒕컻 | ?묒뾽 ?ㅽ뻾 ?붿껌??domain怨?`all_pending`/`targeted` ?ㅽ뻾 紐⑤뱶濡?遺꾧린?섎뒗 Router ?뺣━ | `08_jobExecutionRouter.py` | 1.25 WD |
| ?뚯뒪??| `map_id`/`sql_id`/`space_nm` ?④굔 諛?蹂듭닔嫄?吏?????異붿텧 寃利?| `08_jobExecutionRouter.py` | 0.75 WD |
| ?뚯뒪??| 媛?domain route媛 `09_executionPlanSummary`濡??곌껐?섎뒗吏 寃利?| `08_jobExecutionRouter.py` | 0.5 WD |
| 媛쒕컻 | ?ㅽ뻾 ???묒뾽 ?? ?ㅽ뻾 紐⑤뱶, job list瑜?Chat Output?쇰줈 ?덈궡?섎뒗 而댄룷?뚰듃 ?뺣━ | `09_executionPlanSummary.py` | 1.0 WD |
| ?뚯뒪??| Execution Plan Summary??`Notice`? `Payload` 異쒕젰??媛곴컖 Chat Output/Pipeline?쇰줈 ?곌껐 媛?ν븳吏 寃利?| `09_executionPlanSummary.py` | 0.5 WD |

Job Target Execution Routing ?뚭퀎: 6.0 WD

## 3. DB Migration Pipeline

| 援щ텇 | Task | ?뚯씪 | 湲곌컙 |
|---|---|---|---:|
| 媛쒕컻 | DB Migration ?묒뾽 ????ㅽ뻾 Pipeline??POC ?뚯뒪??寃곌낵 諛섑솚 ?뺥깭濡??뺣━ | `10_migPipeline.py` | 1.0 WD |
| ?뚯뒪??| `map_id=101` 吏???ㅽ뻾 ??`FAIL-TEST`? 濡쒓렇媛 諛섑솚?섎뒗吏 寃利?| `10_migPipeline.py` | 0.5 WD |
| ?뚯뒪??| `08 -> 09 -> 10 -> 13` DB Migration ????ㅽ뻾 ?먮쫫 寃利?| `10_migPipeline.py` | 1.0 WD |

DB Migration ?뚭퀎: 2.5 WD

## 4. SQL Conversion Pipeline

| 援щ텇 | Task | ?뚯씪 | 湲곌컙 |
|---|---|---|---:|
| 媛쒕컻 | SQL Conversion ?묒뾽 ????ㅽ뻾 Pipeline??POC ?뚯뒪??寃곌낵 諛섑솚 ?뺥깭濡??뺣━ | `12_sqlConversionPipeline.py` | 1.0 WD |
| 媛쒕컻 | `selected_jobs`/`planned_jobs`瑜?Pipeline ?낅젰 ?뺤떇?쇰줈 蹂??| `12_sqlConversionPipeline.py` | 0.5 WD |
| ?뚯뒪??| `08 -> 12 -> 13 (+ parallel 09 notice)` SQL Conversion ????ㅽ뻾 ?먮쫫 寃利?| `12_sqlConversionPipeline.py` | 1.0 WD |
| ?뚯뒪??| SQL Conversion ?ㅽ뙣/遺遺??깃났 寃곌낵 payload 寃利?| `12_sqlConversionPipeline.py` | 0.5 WD |

SQL Conversion ?뚭퀎: 3.0 WD

## 5. SQL Tuning Pipeline

| 援щ텇 | Task | ?뚯씪 | 湲곌컙 |
|---|---|---|---:|
| 媛쒕컻 | SQL Tuning ?묒뾽 ????ㅽ뻾 Pipeline??POC ?뚯뒪??寃곌낵 諛섑솚 ?뺥깭濡??뺣━ | `15_sqlTuningPipeline.py` | 1.0 WD |
| 媛쒕컻 | `selected_jobs`/`planned_jobs`瑜?Pipeline ?낅젰 ?뺤떇?쇰줈 蹂??| `15_sqlTuningPipeline.py` | 0.5 WD |
| ?뚯뒪??| `08 -> 15 -> 13 (+ parallel 09 notice)` SQL Tuning ????ㅽ뻾 ?먮쫫 寃利?| `15_sqlTuningPipeline.py` | 1.0 WD |
| ?뚯뒪??| SQL Tuning ?ㅽ뙣/遺遺??깃났 寃곌낵 payload 寃利?| `15_sqlTuningPipeline.py` | 0.5 WD |

SQL Tuning ?뚭퀎: 3.0 WD

## 6. SQL Formatting Pipeline

| 援щ텇 | Task | ?뚯씪 | 湲곌컙 |
|---|---|---|---:|
| 媛쒕컻 | SQL Formatting ?묒뾽 ????ㅽ뻾 Pipeline??POC ?뚯뒪??寃곌낵 諛섑솚 ?뺥깭濡??뺣━ | `17_sqlFormattingPipeline.py` | 1.0 WD |
| 媛쒕컻 | `selected_jobs`/`planned_jobs`瑜?Pipeline ?낅젰 ?뺤떇?쇰줈 蹂??| `17_sqlFormattingPipeline.py` | 0.5 WD |
| ?뚯뒪??| `08 -> 17 -> 13 (+ parallel 09 notice)` SQL Formatting ????ㅽ뻾 ?먮쫫 寃利?| `17_sqlFormattingPipeline.py` | 0.75 WD |
| ?뚯뒪??| SQL Formatting ?ㅽ뙣/遺遺??깃났 寃곌낵 payload 寃利?| `17_sqlFormattingPipeline.py` | 0.5 WD |

SQL Formatting ?뚭퀎: 2.75 WD

## 7. ?꾩껜 異붿젙

| 援щ텇 | 湲곌컙 |
|---|---:|
| 怨듯넻 / Flow | 4.5 WD |
| Management | 7.5 WD |
| Job Target Execution Routing | 6.0 WD |
| DB Migration Pipeline | 2.5 WD |
| SQL Conversion Pipeline | 3.0 WD |
| SQL Tuning Pipeline | 3.0 WD |
| SQL Formatting Pipeline | 2.75 WD |
| 珥앺빀 | 29.25 WD |

???쇱젙? 湲곗〈 援ы쁽 濡쒖쭅??Langflow 而댄룷?뚰듃 ?뺥깭濡?由ы뙥?좊쭅?쒕떎???꾩젣?? ???뚭퀬由ъ쬁 媛쒕컻?대굹 ?꾨＼?꾪듃 ?ъ꽕怨꾨뒗 ?ы븿?섏? ?딅뒗??

