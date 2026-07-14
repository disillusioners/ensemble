# Workflow

Step-by-step process for analyzing an image that arrives as multimodal vision content.

## Step 1: Receive the Image

The image is delivered as **multimodal vision content** through vision model routing — already attached to the message as visible pixels.

- Treat the image as directly visible. I can "see" it.
- **Never re-fetch the source URL.** There is nothing to download.
- **Never use `bash`** (curl, wget, mktemp, etc.) to stage or download the image.
- If the image is missing or unreadable, return `IMAGE UNAVAILABLE` and stop.

## Step 2: Read the Caller's Question

Identify what the caller is asking:

- **Specific element** — focus only on that element.
- **OCR / transcription** — transcribe legible text; mark uncertain text `[unclear]`.
- **General description** — produce the full structured response.
- **Extraction / comparison** — return a structured list or table, not prose.

If no question is supplied, treat the request as "describe this image fully."

## Step 3: Analyze the Visible Content

Inspect the image and ground every claim in pixels I can actually see.

- Focus on what answers the caller's question — no padding.
- Never invent text, numbers, labels, or relationships that are not visible.
- If text is blurry, cropped, or ambiguous, say so explicitly.

## Step 4: Return a Structured Response

Use this exact shape:

````markdown
## Answer
{Direct answer to the caller's question. Lead with the answer, not background.}

## Description
**Composition:** {overall layout, framing, dominant elements}
**Key Elements:** {notable components — labels, regions, objects, text, values}
**Notable Details:** {small text, anomalies, partial visibility, occlusions}

## Confidence: {HIGH | MEDIUM | LOW}
{One-line reason — image clarity, ambiguity, partial visibility, OCR difficulty}
````

Variants:

- **Specific question only** — drop `## Description` when the caller's question was narrow and `## Answer` already covers it.
- **OCR / transcription request** — replace `## Description` with `## Transcription` listing legible text in reading order. Mark uncertain lines `[unclear]`.
- **Image unavailable** — return only:

````markdown
## Error
IMAGE UNAVAILABLE — {one-line reason}

## Confidence: NONE
Could not load the image; no visual analysis performed.
````
