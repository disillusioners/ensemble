# Workflow

Step-by-step process for reading and explaining an image.

---

## Step 1: Parse the Request

The caller's message contains two pieces of information I need:

- **Source** — either an `http(s)://` URL or a local file path
- **Question / focus** — what the caller wants to know about the image (general description, specific element, OCR, comparison, etc.)

Identify the source type:

| Input shape | Source type | Action |
|-------------|-------------|--------|
| Starts with `http://` or `https://` | Remote URL | Go to Step 2a |
| Absolute or `./` relative path on disk | Local file | Go to Step 2b |
| Starts with `file://` | Local URL form | Convert to a local path, then Step 2b |
| Anything else | Unrecognized | Return `UNREADABLE SOURCE` — describe what was supplied and stop |

If no question is provided, treat the request as "describe this image fully" and produce the full structured output in Step 5.

---

## Step 2a: Fetch a Remote URL

Use a per-instance temp file to avoid collisions between concurrent image-reader instances.

```bash
TMPFILE=$(mktemp /tmp/image_reader_XXXXXX.${EXT})

# Download the image
curl -fsSL "$URL" -o "$TMPFILE"
FETCH_EXIT=$?

# Inspect
if [ $FETCH_EXIT -ne 0 ] || [ ! -s "$TMPFILE" ]; then
  rm -f "$TMPFILE"
  echo "FETCH FAILED"
  return
fi

echo "FETCH OK: $TMPFILE"
```

Rules:

- Use `curl -fsSL` (fail on HTTP errors, follow redirects silently, no progress meter).
- If `curl` is not available, fall back to `wget -q "$URL" -O "$TMPFILE"` once.
- If both fail, return `FETCH FAILED` with the curl/wget error message — do not retry.
- The temp file extension should match the URL's MIME hint (or default to `.png`) so downstream tools can identify the format.

Proceed to Step 3 with the temp file path.

---

## Step 2b: Read a Local Path

Confirm the file is readable and supported:

```bash
# Confirm exists and is a regular file
test -f "$PATH" || { echo "NOT FOUND: $PATH"; return; }

# Confirm a supported extension
case "$PATH" in
  *.png|*.jpg|*.jpeg|*.gif|*.webp|*.bmp|*.svg|*.tiff)
    echo "LOCAL OK: $PATH"
    ;;
  *)
    echo "UNSUPPORTED FORMAT: $PATH"
    return
    ;;
esac
```

If the extension is unsupported but the file exists, return `UNSUPPORTED FORMAT` listing the supported types — do not attempt to read it.

Proceed to Step 3 with the original path.

---

## Step 3: Load the Image

Open the image with the filesystem/image-read tool. Confirm the load succeeded:

- Image dimensions are reported
- Image content is accessible (not a 0-byte file, not a corrupt header)
- The format matches the extension

If the load fails, return `LOAD FAILED` with the underlying error and stop. Do not proceed to description.

---

## Step 4: Inspect & Ground in the Caller's Question

Read the image and form observations that directly address the caller's question. Anchor every claim in something visible in the pixels.

- If the caller asked about a **specific element** (e.g., "what does this button say?"): focus on that element and answer precisely. Do not pad with unrelated description.
- If the caller asked for **OCR / transcription**: transcribe only what is legible. Mark uncertain text as `[unclear]`.
- If the caller asked for a **general description**: produce the full structured output in Step 5.
- If the caller asked for **comparison or extraction** (e.g., "list the chart values"): produce a structured table or list, not prose.

### Grounding checklist

Before returning, confirm every claim:

- [ ] Could I point to the pixel region that supports this claim?
- [ ] If the text is blurry or cropped, did I mark it `[unclear]` rather than guess?
- [ ] If the question was specific, did I lead with the direct answer?

---

## Step 5: Return a Structured Response

Use this exact shape — every section is required:

````markdown
## Source
{URL or path loaded, plus a one-line confirmation: "Loaded successfully — {WxH}, {format}"}

## Answer
{Direct answer to the caller's question. Lead with the answer, not background.}

## Description
**Composition:** {overall layout, framing, aspect, dominant visual elements}
**Key Elements:** {bulleted list of the notable components — labels, regions, objects, text, values}
**Notable Details:** {anything the caller might care about — small text, anomalies, partial visibility, occlusions}

## Confidence: {HIGH | MEDIUM | LOW}
{One-line reason — image clarity, ambiguity, partial visibility, OCR difficulty, etc.}
````

Variants:

- **Specific question only** — drop `## Description` if the caller's question was narrow (e.g., "what color is the header?") and the `## Answer` already covers it.
- **OCR / transcription request** — replace `## Description` with `## Transcription` and list legible text in reading order. Mark uncertain lines `[unclear]`.
- **Load failure** — return only:
  ````markdown
  ## Source
  {URL or path}
  
  ## Error
  {FETCH FAILED | NOT FOUND | UNSUPPORTED FORMAT | LOAD FAILED} — {underlying error}
  
  ## Confidence: NONE
  Could not load the image; no visual analysis performed.
  ````

---

## Step 6: Clean Up

If a temp file was created in Step 2a, remove it after the response is composed:

```bash
rm -f "$TMPFILE"
```

Do not leave temp files behind — concurrent runs will fill `/tmp`.

---

## Summary

```
Step 1: Parse request → identify URL vs path, extract question
  ↓
Step 2a: Fetch URL to mktemp file (curl/wget)    ┐
  or                                              ├─→ temp file or local path
Step 2b: Validate local path + extension         ┘
  ↓
Step 3: Load image → confirm dimensions + format
  ↓
Step 4: Inspect → ground every claim in visible pixels, focus on the caller's question
  ↓
Step 5: Return structured response (Source, Answer, Description, Confidence)
  ↓
Step 6: rm -f any temp file
```