# ClausePath demo video — narration (Korean)

Same scenes and sentence numbers as `narration-en.md`. Recorded with a Korean traveler (김지수, Plus plan) and the
Korean dashboard: `DEMO_LANG=ko demo/record_demo.py`. Voice: Amazon Polly neural `Seoyeon`.
Render: `DEMO_LANG=ko .venv/bin/python demo/make_rough_cut.py demo/recording/<run>-ko`

```xml
<!-- 1 --><speak><prosody rate="110%">태풍으로 지수 씨가 타려던 비행기가 결항됐습니다.<break time="450ms"/> 어젯밤 호텔비는 보상될까요?<break time="450ms"/> 보통은 답을 듣기까지 며칠, 길게는 몇 주가 걸립니다.<break time="450ms"/> 여기서는 1분도 안 걸립니다. 약관 근거와 함께요.</prosody></speak>
<!-- 2 --><speak><prosody rate="110%"><sub alias="클로즈패스">ClausePath</sub>는 여행자보험사를 위한 서비스입니다.<break time="450ms"/> 보험사는 결항 항공편을 한 번만 등록합니다.<break time="450ms"/> 그러면 지연 담보가 있는 여행자 모두에게 메시지가 갑니다.<break time="450ms"/> 사람마다 채널을 고르고, 그 이유를 남깁니다.</prosody></speak>
<!-- 3 --><speak><prosody rate="110%">지수 씨가 문자로 질문합니다.<break time="450ms"/> 에이전트도 문자로 답합니다.</prosody></speak>
<!-- 4 --><speak><prosody rate="110%">문자 속 링크를 누르면 같은 대화가 웹 채팅으로 이어집니다.<break time="450ms"/> 앱 설치도,<break time="300ms"/> 로그인도 필요 없습니다.<break time="450ms"/> 지수 씨가 어젯밤 호텔비를 묻습니다.<break time="450ms"/> 에이전트는 아마존 베드록의 클로드로 동작합니다.<break time="450ms"/> 보장에 관한 답에는 항상 약관 근거가 붙습니다.<break time="450ms"/> 번호를 누르면 해당 조항이 바로 보입니다.<break time="450ms"/> 이어서 조항 지도를 엽니다.<break time="450ms"/> 지수 씨의 플랜은 하루 150달러가 한도입니다.<break time="450ms"/> 그리고 미니바는 보상하지 않습니다.<break time="450ms"/> 지수 씨가 몰랐던 내용입니다.</prosody></speak>
<!-- 5 --><speak><prosody rate="110%">AI가 사진을 한 장씩 읽습니다.<break time="450ms"/> 날짜와 항공편, 장소는 규칙으로 확인합니다.<break time="450ms"/> 흐린 사진은 그 자리에서 다시 찍어 달라고 요청합니다.<break time="450ms"/> 다시 찍은 영수증은 바로 읽히고, 미니바 맥주는 빠집니다.<break time="450ms"/> 계좌 정보는 보안 페이지에서만 받습니다.</prosody></speak>
<!-- 6 --><speak><prosody rate="110%">청구서와 서류는 아마존 <sub alias="에스이에스">SES</sub>로 보험사에 전달됩니다.<break time="450ms"/> 심사자는 영수증마다 무엇을 읽었고, 어디서 걸렸는지 봅니다.<break time="450ms"/> 미니바가 이미 빠져 있는 것도 보입니다.<break time="450ms"/> 100달러가 넘는 지급은 두 번째 심사자가 확인합니다.<break time="450ms"/> 결정은 먼저 이메일로, 이어서 채팅으로 지수 씨에게 갑니다.</prosody></speak>
<!-- 7 --><speak><prosody rate="110%">심사자가 승인이라는 말을 입력합니다.<break time="450ms"/> 차단됩니다.<break time="450ms"/> 잘못된 한 문장이 약속처럼 들릴 수 있기 때문입니다.<break time="450ms"/> 규칙과 아마존 베드록 가드레일이 자유롭게 쓴 모든 메시지를 검사합니다. 에이전트의 말도요.<break time="450ms"/> 결정은 정해진 템플릿으로만 나갑니다.</prosody></speak>
<!-- 8a --><speak><prosody rate="110%">항공편 결항.<break time="1100ms"/> 문자로 안내.<break time="300ms"/> 같은 대화를 웹으로.<break time="700ms"/> 클로드가 답하고, 가드레일이 검사.<break time="300ms"/> 서류는 보험사로.<break time="300ms"/> 결정은 사람이.</prosody></speak>
<!-- 8b --><speak><prosody rate="110%">이메일은 지금 실제로 발송됩니다.<break time="450ms"/> 문자와 <sub alias="알씨에스">RCS</sub>는 <sub alias="에이더블유에스">AWS</sub> 엔드 유저 메시징으로 만들었습니다.<break time="450ms"/> 이 데모에서는 통신사 발송을 시뮬레이션했습니다.</prosody></speak>
<!-- 8n --><speak><prosody rate="110%">공개 데이터셋의 실제 일본 영수증 사진 천여 장에서, 합계 금액을 99.7퍼센트 정확하게 읽었습니다.</prosody></speak>
<!-- 8c --><speak><prosody rate="110%">지수 씨는 1분 안에 답을 받았습니다.<break time="450ms"/> 청구 준비는 에이전트가 합니다.<break time="450ms"/> 결정은 보험사가 합니다.</prosody></speak>
<!-- 8d --><speak><prosody rate="110%"><sub alias="클로즈패스">ClausePath</sub>.<break time="450ms"/> 근거가 있는 답.<break time="450ms"/> 결정은 사람이.</prosody></speak>
```
