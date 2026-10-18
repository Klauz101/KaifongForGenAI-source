# Kaifong 街坊

## Hackathon project overview

Kaifong is a safety-first wellbeing check-in platform for older people living
alone. It turns short, friendly conversations into structured wellbeing records
and a prioritised work queue for social workers.

The project is designed around a simple idea: technology should help care
workers notice change earlier and spend more time building human connection,
not replace human judgement.

## What the current prototype demonstrates

The working prototype runs locally in **mock mode with synthetic data and no API
keys**. It includes:

- A browser-based phone simulator with a type-in fallback.
- Conversation flows in **Cantonese and English**.
- Cantonese text input, including Jyutping support in the simulator.
- Template-based mock conversation replies so the demo works offline.
- Deterministic red-flag screening before any language-model step.
- Fixed safety responses and live RED alerts for supported emergency phrases.
- A post-call structured wellbeing record with evidence turns.
- Personal-baseline risk triage using Green, Amber, and Red levels.
- Memory facts that can be recalled in later conversations.
- Connection nudges towards family, neighbours, outings, or community activities.
- A care-worker dashboard with today’s prioritised caseload.
- Elder detail pages with trends, call history, summaries, and evidence.
- Alert acknowledgement, resolution, mandatory outcome notes, and action logs.
- Role-based access for administrators, social workers, and family accounts.
- Family digest previews that do not expose transcripts or clinical detail.
- Consent gates, encrypted sensitive fields, pseudonymisation, audit logs,
  retention handling, export, correction, and withdrawal workflows.
- Synthetic seed data covering eight elders and 21 days of history.
- Offline evaluation tooling for red flags, extraction, triage, latency, and
  speech clips when available.

## Languages

Kaifong currently supports:

- **Cantonese (`yue`)** as the primary intended language.
- **English (`en`)** for testing, demonstrations, accessibility, and
  non-Cantonese-speaking team members.
- English subtitles and bilingual dashboard content where applicable.
- Traditional Chinese UI strings with English translations.
- Jyutping input conversion for the Cantonese simulator.

The conversation resources and safety wording are maintained separately from
application code so they can be reviewed and improved by native Cantonese
speakers.

## Demonstration user flow

1. A social worker signs in to the care-worker dashboard.
2. The dashboard highlights people who need attention and explains the
   contributing reasons.
3. The worker opens an elder’s record and starts the phone simulator.
4. Kaifong conducts a short conversation about everyday wellbeing, including
   food, sleep, medication, pain, going out, mood, and social contact.
5. The worker can reply by typing Cantonese, Jyutping, or English. Browser speech
   recognition may also be available depending on the browser.
6. Every non-empty elder message passes through the deterministic safety screen
   before unclear-speech handling, opt-out handling, or any LLM request.
7. After the call, the system extracts structured fields, links them to
   evidence turns, creates a bilingual summary, updates memory, and recalculates
   risk.
8. The social worker reviews the evidence, follows up with the person, and
   records a human action.

## System architecture

### Interface layer

The current demo uses a browser phone simulator. A telephony adapter is also
implemented behind an interface, including a Twilio path with consent,
allowlist, and webhook-signature safeguards, but a real outbound phone call has
not yet been verified.

### Conversation and provider layer

Speech-to-text, text-to-speech, translation, and LLM services are abstracted
behind provider interfaces. The default providers are mock implementations so
the project works without credentials.

The architecture can be configured for compatible external LLM endpoints and
for supported speech providers. Provider selection is configuration-driven
rather than hard-wired into the application.

### Safety layer

Safety does not depend on the LLM. The red-flag detector runs before the model
and can only add an escalation. It supports emergency categories such as falls,
cardiac or breathing symptoms, stroke signs, acute illness, and self-harm.

Flagged input produces a scripted response and a human alert. The LLM cannot
remove a flag, resolve an alert, or make a diagnosis.

### Analysis layer

The application supports two extraction paths:

- In mock mode, an offline rule-based extractor identifies supported English
  and Cantonese patterns and composes summaries from reviewed resource
  snippets.
- With a configured LLM provider, the transcript is pseudonymised, sent for
  structured extraction, validated against a Pydantic schema, checked for
  evidence, and safely downgraded to unknown values when evidence is missing.

Risk is calculated by the local rule engine against the person’s own baseline.
Green means “no configured flags,” never “safe.”

### Data and privacy layer

The backend uses FastAPI, SQLModel, SQLite, Pydantic, and Fernet field
encryption. Sensitive text is not placed in audit logs. Raw audio retention is
disabled by default. Access is scoped by role and social-worker caseload.

This is an engineering prototype and is not legal advice. Any real deployment
requires formal privacy, safeguarding, security, and clinical/operational
review.

### Presentation layer

The React and TypeScript dashboard provides:

- Today’s triage view
- Elder records and trend charts
- Call details and evidence
- Live alert notifications
- Alert action workflows
- Family digest views
- Admin metrics, audit, and demo controls
- English and Traditional Chinese UI switching

## What is implemented versus what remains to validate

### Implemented for the prototype

- End-to-end local browser demonstration.
- Cantonese and English conversation modes.
- Mock providers with zero-key startup.
- Deterministic red-flag and output-guard controls.
- Structured extraction and evidence validation.
- Personal-baseline risk rules.
- Auth, roles, consent, audit, encryption, and privacy operations.
- Synthetic longitudinal demo data.
- Automated backend test suite and frontend type-checking.

### Not yet validated as a real-world service

- Speech-recognition error rate on older Cantonese voices.
- Real-provider Cantonese speech quality.
- Median live end-to-end latency.
- Cost per call and monthly cost per person.
- Real outbound phone-call completion.
- Real-user usability and accessibility testing.
- Native-speaker review of every Cantonese phrase.
- Production deployment, monitoring, backup, and incident response.

The current evaluation results are based on developer-authored synthetic cases.
They demonstrate that the software behaves as designed on those cases; they are
not claims of accuracy on real older people.

## Safety and product boundaries

Kaifong:

- Does not diagnose depression, dementia, or any other illness.
- Does not provide medical advice.
- Does not automatically resolve alerts.
- Does not replace emergency services or a social worker.
- Treats the LLM as an assistant, never as the safety authority.
- Uses human review for follow-up and alert resolution.
- Uses synthetic data in the demo.

The safety detector currently performs strongly on its authored core cases but
does not catch every possible paraphrase. This limitation must be addressed
through broader testing and supervised human review before real-world use.

