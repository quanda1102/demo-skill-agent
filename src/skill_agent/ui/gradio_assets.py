from __future__ import annotations

EXAMPLE_PROMPTS = [
    ["Create a skill that extracts top 5 url from a url"],
    ["Covert https://vnexpress.net/ into markdown file "],
    ["Debug why the generated SKILL.md is failing validation and suggest a fix."],
]

APP_CSS = """
:root {
  --paper: #f6f0e4;
  --paper-deep: #eee5d3;
  --panel: rgba(255, 250, 242, 0.9);
  --panel-strong: #fffdf9;
  --ink: #241e16;
  --ink-soft: #645847;
  --line: rgba(88, 67, 36, 0.14);
  --shadow: 0 18px 48px rgba(66, 49, 22, 0.12);
  --ok: #1a6a46;
  --warn: #9a6a00;
  --error: #9a2b20;
}

body,
.gradio-container {
  background:
    radial-gradient(circle at top left, rgba(225, 166, 72, 0.22), transparent 30%),
    radial-gradient(circle at bottom right, rgba(161, 124, 71, 0.14), transparent 28%),
    linear-gradient(180deg, var(--paper) 0%, var(--paper-deep) 100%);
  color: var(--ink);
}

.gradio-container {
  max-width: 1320px !important;
  padding: 24px !important;
}

.hero-card,
.panel-card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 22px;
  box-shadow: var(--shadow);
}

.panel-card {
  padding: 10px !important;
}

#chatbot,
#trace-box {
  border-radius: 18px;
  overflow: hidden;
}

#chatbot {
  min-height: 560px;
}

#message-box textarea {
  font-size: 1rem !important;
  line-height: 1.55 !important;
}

#trace-box {
  font-size: 13px !important;
  line-height: 1.6 !important;
  min-height: 480px;
  padding: 12px !important;
}

.trace-toggle {
  margin-bottom: 10px;
}

.gradio-button {
  border-radius: 999px !important;
}

/* Tool call accordion blocks inside chat bubbles */
#chatbot .message-wrap details {
  border-left: 3px solid #818cf8;
  border-radius: 6px;
  background: rgba(129, 140, 248, 0.07);
  padding: 5px 10px;
  margin: 6px 0 2px;
}
#chatbot .message-wrap details summary {
  color: #4f46e5;
  font-size: 0.82em;
  font-weight: 600;
  cursor: pointer;
  list-style: none;
  letter-spacing: 0.01em;
}
#chatbot .message-wrap details summary::-webkit-details-marker { display: none; }
#chatbot .message-wrap details pre,
#chatbot .message-wrap details code {
  font-size: 0.8em !important;
  color: var(--ink-soft);
}

@media (max-width: 900px) {
  .gradio-container {
    padding: 14px !important;
  }

  .hero-card,
  .panel-card {
    border-radius: 18px;
  }

  #chatbot {
    min-height: 420px;
  }
}
"""
