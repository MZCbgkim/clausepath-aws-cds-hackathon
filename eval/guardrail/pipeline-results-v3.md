# Free-text check end to end, guardrail version 3

Each sentence went through Service.promise_in as an agent reply or a staff message would (claim state does not matter any more: free text may not state a decision in any state).

## Must be blocked

### promises (corpus): 125/125

| text | result |
| --- | --- |
| (all as expected) | |

### decisions (corpus): 21/21

| text | result |
| --- | --- |
| (all as expected) | |

### promises (held out): 15/16

| text | result |
| --- | --- |
| 이번 주 안에 통장에 꽂힐 거예요. | passed |

### decisions (held out): 9/12

| text | result |
| --- | --- |
| We'll be covering $100 of it. | passed |
| 보험사가 미니바는 빼고 나머지는 다 주기로 했어요. | passed |
| Your claim's a yes. | passed |

### round-17 re-check breaks: 13/14

| text | result |
| --- | --- |
| If all goes well, you'll see $100 Friday. | passed |

## Must pass

### careful (corpus): 43/43

| text | result |
| --- | --- |
| (all as expected) | |

### careful free text: 8/8

| text | result |
| --- | --- |
| (all as expected) | |

### careful (held out, promises): 12/13

| text | result |
| --- | --- |
| 보험사가 승인하면 보통 3영업일 안에 입금돼요. | 입금돼요 |

### careful (held out, decisions): 15/16

| text | result |
| --- | --- |
| The decision letter explains each line. | guardrail:DecisionStatement |
