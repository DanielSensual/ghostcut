#!/usr/bin/env node
/**
 * Ghost Captions Engine V2
 * ════════════════════════
 * Viral word-by-word highlight captions (Hormozi/CapCut style)
 * Uses OpenAI Whisper word timestamps → generates ASS subtitles → FFmpeg burn-in
 *
 * Usage:
 *   # Auto-extract audio from video + transcribe + burn captions
 *   node ghost-captions.cjs --video input.mp4 --output final.mp4
 *
 *   # Use separate audio file for transcription
 *   node ghost-captions.cjs --video input.mp4 --audio voiceover.mp3 --output final.mp4
 *
 *   # Use pre-generated Whisper word timestamps
 *   node ghost-captions.cjs --video input.mp4 --words /tmp/whisper-words.json --output final.mp4
 *
 *   # Generate ASS subtitle only (no burn-in) for use in FCP/Premiere/Resolve
 *   node ghost-captions.cjs --video input.mp4 --ass-only --output captions.ass
 *
 * Options:
 *   --video       Input video file (required)
 *   --audio       Audio file to transcribe (optional — auto-extracted if omitted)
 *   --words       Pre-generated Whisper JSON with word timestamps (skip transcription)
 *   --output      Output video or .ass file (default: output-captioned.mp4)
 *   --style       Caption style preset (default: hormozi)
 *   --highlight   Highlight color: hex "#FF00FF" or preset name "cyan" (default: cyan)
 *   --grouping    Words per group: 2, 3, or 4 (default: 3)
 *   --position    Vertical position: "middle" | "lower" | "upper" (default: middle)
 *   --font        Font name (default: Montserrat — auto-detects availability)
 *   --pop         Word pop scale percentage (default: 120)
 *   --ass-only    Generate ASS file only, skip FFmpeg burn-in
 *   --save-words  Save Whisper word timestamps JSON for reuse
 *   --help        Show this help
 *
 * Style Presets:
 *   captions  → Captions app style: big Poppins, purple highlight, bottom position (default)
 *   hormozi   → White text, cyan highlight, thick outline, ALL CAPS
 *   neon      → Electric purple/pink glow, thin outline
 *   fire      → Orange-red highlight, bold shadow
 *   ghost     → Ghost AI brand cyan/dark theme
 *   bold      → Classic white + highlight, medium weight
 *   minimal   → Clean, thin, subtle
 *
 * Color Presets:
 *   cyan, red, yellow, green, pink, orange, purple, white, gold
 */

const { execSync, spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

// ─── Parse CLI args ──────────────────────────────────────────
const args = process.argv.slice(2);
function getArg(name, fallback = null) {
  const idx = args.indexOf(`--${name}`);
  return idx !== -1 && args[idx + 1] && !args[idx + 1].startsWith('--') ? args[idx + 1] : fallback;
}
function hasFlag(name) {
  return args.includes(`--${name}`);
}

const VIDEO = getArg('video');
const AUDIO = getArg('audio');
const WORDS_FILE = getArg('words');
const OUTPUT = getArg('output', 'output-captioned.mp4');
const STYLE = getArg('style', 'captions');
const GROUPING_RAW = getArg('grouping');
const POSITION_RAW = getArg('position');
const FONT_OVERRIDE = getArg('font');
const POP_SCALE_RAW = getArg('pop');
const ASS_ONLY = hasFlag('ass-only');
const SAVE_WORDS = hasFlag('save-words');

// ─── Color Presets ───────────────────────────────────────────
const COLOR_PRESETS = {
  cyan:    '#00FFFF',
  red:     '#FF3333',
  yellow:  '#FFD700',
  green:   '#00FF88',
  pink:    '#FF69B4',
  orange:  '#FF6B35',
  purple:  '#B366FF',
  white:   '#FFFFFF',
  gold:    '#FFB800',
  ghost:   '#00E5FF',  // Ghost AI brand
};

function resolveHighlight(input, style) {
  // Style-specific defaults
  if (!input && style === 'captions') return '#B388FF'; // lavender purple
  if (!input) return '#00FFFF'; // cyan
  if (input.startsWith('#')) return input;
  return COLOR_PRESETS[input.toLowerCase()] || '#00FFFF';
}

const HIGHLIGHT_HEX = resolveHighlight(getArg('highlight'), STYLE);

// Style-specific defaults
const GROUPING = GROUPING_RAW ? parseInt(GROUPING_RAW, 10) : (STYLE === 'captions' ? 5 : 3);
const POSITION = POSITION_RAW || (STYLE === 'captions' ? 'lower' : 'middle');
const POP_SCALE = POP_SCALE_RAW ? parseInt(POP_SCALE_RAW, 10) : (STYLE === 'captions' ? 100 : 120);

// ─── Font Detection ──────────────────────────────────────────
function detectFont() {
  if (FONT_OVERRIDE) return FONT_OVERRIDE;

  // Style-specific font preferences
  const fontPrefs = STYLE === 'captions'
    ? ['Poppins Black', 'Poppins', 'Montserrat', 'Arial Black', 'Helvetica Neue']
    : ['Montserrat', 'Arial Black', 'Futura', 'Helvetica Neue'];
  try {
    const installed = execSync('fc-list', { encoding: 'utf8' });
    for (const font of fontPrefs) {
      if (installed.toLowerCase().includes(font.toLowerCase())) return font;
    }
  } catch {
    // fc-list not available, fall back
  }
  return 'Helvetica Neue'; // macOS always has this
}

const FONT = detectFont();

// ─── Help ────────────────────────────────────────────────────
if (hasFlag('help') || !VIDEO) {
  console.log(`
╔══════════════════════════════════════╗
║   👻 GHOST CAPTIONS ENGINE V2       ║
║   Word-by-word viral highlights     ║
╚══════════════════════════════════════╝

Usage:
  node ghost-captions.cjs --video input.mp4 --output final.mp4
  node ghost-captions.cjs --video input.mp4 --audio voice.mp3 --output final.mp4
  node ghost-captions.cjs --video input.mp4 --words timestamps.json --output final.mp4

Options:
  --video       Input video file (required)
  --audio       Audio file to transcribe (auto-extracted from video if omitted)
  --words       Pre-generated Whisper JSON (skip transcription)
  --output      Output file (default: output-captioned.mp4)
  --style       ${Object.keys(getStylePresets()).join(' | ')} (default: captions)
  --highlight   Color: hex "#FF00FF" or name: ${Object.keys(COLOR_PRESETS).join(', ')}
  --grouping    Words per group: 2, 3, or 4 (default: 3)
  --position    middle | lower | upper (default: middle)
  --font        Font name (default: auto-detect)
  --pop         Word pop scale % (default: 120)
  --ass-only    Generate .ass subtitle file only (for FCP/Premiere/Resolve)
  --save-words  Save Whisper timestamps JSON for reuse
  --help        Show this help

Examples:
  # Quick — just give it a video, it handles everything
  node ghost-captions.cjs --video reel.mp4

  # Fire style with gold highlights
  node ghost-captions.cjs --video reel.mp4 --style fire --highlight gold

  # Generate subtitle file for external editor
  node ghost-captions.cjs --video reel.mp4 --ass-only --output captions.ass
`);
  process.exit(VIDEO ? 0 : 1);
}

// ─── Color utils ─────────────────────────────────────────────
// ASS uses &HAABBGGRR format (alpha, blue, green, red)
function hexToASS(hex) {
  const r = hex.slice(1, 3);
  const g = hex.slice(3, 5);
  const b = hex.slice(5, 7);
  return `&H00${b}${g}${r}`.toUpperCase();
}

function hexToASSWithAlpha(hex, alpha = '00') {
  const r = hex.slice(1, 3);
  const g = hex.slice(3, 5);
  const b = hex.slice(5, 7);
  return `&H${alpha}${b}${g}${r}`.toUpperCase();
}

// ─── Step 1: Auto-extract audio from video ───────────────────
function extractAudioFromVideo(videoPath) {
  const audioPath = path.join('/tmp', `ghost-captions-audio-${Date.now()}.mp3`);
  console.log('🎵 Extracting audio from video...');
  execSync(`ffmpeg -y -i "${videoPath}" -vn -acodec libmp3lame -q:a 2 "${audioPath}" 2>/dev/null`, {
    stdio: 'pipe'
  });
  console.log(`   ✅ Audio extracted: ${audioPath}\n`);
  return audioPath;
}

// ─── Step 2: Get word-level timestamps ───────────────────────
function getWordTimestamps(audioSource) {
  if (WORDS_FILE) {
    console.log(`📄 Loading word timestamps from ${WORDS_FILE}`);
    return JSON.parse(fs.readFileSync(WORDS_FILE, 'utf8'));
  }

  console.log('🎤 Transcribing with Whisper (word-level timestamps)...');
  const apiKey = process.env.OPENAI_API_KEY;
  if (!apiKey) {
    console.error('❌ OPENAI_API_KEY environment variable required');
    process.exit(1);
  }

  const result = spawnSync('curl', [
    '-s',
    'https://api.openai.com/v1/audio/transcriptions',
    '-H', `Authorization: Bearer ${apiKey}`,
    '-F', `file=@${audioSource}`,
    '-F', 'model=whisper-1',
    '-F', 'response_format=verbose_json',
    '-F', 'timestamp_granularities[]=word',
    '-F', 'language=en'
  ], { encoding: 'utf8', maxBuffer: 50 * 1024 * 1024 });

  if (result.error) throw result.error;

  const data = JSON.parse(result.stdout);

  if (SAVE_WORDS) {
    const wordsPath = path.join(path.dirname(VIDEO), `${path.basename(VIDEO, path.extname(VIDEO))}-words.json`);
    fs.writeFileSync(wordsPath, JSON.stringify(data, null, 2));
    console.log(`   💾 Saved word timestamps: ${wordsPath}`);
  }

  return data;
}

// ─── Step 3: Group words into display chunks ─────────────────
function groupWords(words, groupSize) {
  const groups = [];
  for (let i = 0; i < words.length; i += groupSize) {
    const chunk = words.slice(i, i + groupSize);
    groups.push({
      words: chunk,
      start: chunk[0].start,
      end: chunk[chunk.length - 1].end,
      text: chunk.map(w => w.word).join(' ')
    });
  }
  return groups;
}

// ─── Step 4: Generate ASS subtitle file ──────────────────────
function formatTime(seconds) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return `${h}:${String(m).padStart(2, '0')}:${s.toFixed(2).padStart(5, '0')}`;
}

function getStylePresets() {
  const highlightColor = hexToASS(HIGHLIGHT_HEX);
  const whiteColor = '&H00FFFFFF';
  const blackColor = '&H00000000';
  const shadowColor = hexToASSWithAlpha('#000000', '80');

  const posMargin = {
    middle: 580,
    upper: 800,
    lower: 200,
  };
  const mv = posMargin[POSITION] || 580;

  return {
    captions: {
      fontName: FONT,
      fontSize: 52,
      primaryColor: whiteColor,
      secondaryColor: highlightColor,
      outlineColor: blackColor,
      backColor: hexToASSWithAlpha('#000000', '40'),
      outline: 5,
      shadow: 0,
      bold: 1,
      alignment: 2,
      marginV: POSITION_RAW ? mv : 480,  // eye-level positioning
      marginL: 30,
      marginR: 30,
      spacing: 1,
      borderStyle: 1,
    },
    hormozi: {
      fontName: FONT,
      fontSize: 32,
      primaryColor: whiteColor,
      secondaryColor: highlightColor,
      outlineColor: blackColor,
      backColor: shadowColor,
      outline: 4,
      shadow: 0,
      bold: 1,
      alignment: 2,
      marginV: mv,
      marginL: 40,
      marginR: 40,
      spacing: 2,
      borderStyle: 1,
    },
    neon: {
      fontName: FONT,
      fontSize: 30,
      primaryColor: whiteColor,
      secondaryColor: highlightColor,
      outlineColor: hexToASS('#9B30FF'),
      backColor: hexToASSWithAlpha('#6A0DAD', '60'),
      outline: 3,
      shadow: 4,
      bold: 1,
      alignment: 2,
      marginV: mv,
      marginL: 40,
      marginR: 40,
      spacing: 1,
      borderStyle: 1,
    },
    fire: {
      fontName: FONT,
      fontSize: 34,
      primaryColor: whiteColor,
      secondaryColor: hexToASS('#FF6B35'),
      outlineColor: hexToASS('#8B0000'),
      backColor: hexToASSWithAlpha('#2D0000', '90'),
      outline: 4,
      shadow: 3,
      bold: 1,
      alignment: 2,
      marginV: mv,
      marginL: 35,
      marginR: 35,
      spacing: 2,
      borderStyle: 1,
    },
    ghost: {
      fontName: FONT,
      fontSize: 30,
      primaryColor: whiteColor,
      secondaryColor: hexToASS('#00E5FF'),
      outlineColor: hexToASS('#0A1628'),
      backColor: hexToASSWithAlpha('#0D1117', 'AA'),
      outline: 3,
      shadow: 2,
      bold: 1,
      alignment: 2,
      marginV: mv,
      marginL: 45,
      marginR: 45,
      spacing: 2,
      borderStyle: 1,
    },
    bold: {
      fontName: FONT,
      fontSize: 28,
      primaryColor: whiteColor,
      secondaryColor: highlightColor,
      outlineColor: blackColor,
      backColor: shadowColor,
      outline: 3,
      shadow: 2,
      bold: 1,
      alignment: 2,
      marginV: mv,
      marginL: 50,
      marginR: 50,
      spacing: 1,
      borderStyle: 1,
    },
    minimal: {
      fontName: FONT,
      fontSize: 22,
      primaryColor: whiteColor,
      secondaryColor: highlightColor,
      outlineColor: blackColor,
      backColor: '&H00000000',
      outline: 2,
      shadow: 1,
      bold: 0,
      alignment: 2,
      marginV: mv,
      marginL: 60,
      marginR: 60,
      spacing: 0,
      borderStyle: 1,
    },
  };
}

function generateASS(groups) {
  const styles = getStylePresets();
  const s = styles[STYLE] || styles.hormozi;
  const highlightColor = hexToASS(HIGHLIGHT_HEX);
  const whiteColor = '&H00FFFFFF';

  // For neon style, use its unique outline as highlight color
  const activeColor = STYLE === 'neon' ? hexToASS('#E040FB') : highlightColor;

  let ass = `[Script Info]
Title: Ghost Captions V2
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,${s.fontName},${s.fontSize},${s.primaryColor},${s.secondaryColor},${s.outlineColor},${s.backColor},${s.bold},0,0,0,100,100,${s.spacing},0,${s.borderStyle},${s.outline},${s.shadow},${s.alignment},${s.marginL},${s.marginR},${s.marginV},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
`;

  for (const group of groups) {
    for (let wi = 0; wi < group.words.length; wi++) {
      const word = group.words[wi];
      const nextWord = group.words[wi + 1];
      const wordEnd = nextWord ? nextWord.start : group.end;

      const lineParts = group.words.map((w, idx) => {
        const cleanWord = w.word.replace(/[{}\\]/g, '');
        if (idx === wi) {
          return `{\\c${activeColor}\\fscx${POP_SCALE}\\fscy${POP_SCALE}}${cleanWord.toUpperCase()}{\\c${whiteColor}\\fscx100\\fscy100}`;
        }
        return cleanWord.toUpperCase();
      });

      const lineText = lineParts.join(' ');
      const start = formatTime(word.start);
      const end = formatTime(Math.min(wordEnd, group.end + 0.05));

      ass += `Dialogue: 0,${start},${end},Default,,0,0,0,,${lineText}\n`;
    }
  }

  return ass;
}

// ─── Step 5: Burn subtitles with FFmpeg ──────────────────────
function burnSubtitles(assFile, videoFile, outputFile) {
  console.log('🔥 Burning captions with FFmpeg...');

  const escapedPath = assFile.replace(/\\/g, '\\\\').replace(/:/g, '\\:').replace(/'/g, "'\\'");

  const cmd = `ffmpeg -y -i "${videoFile}" -vf "ass='${escapedPath}'" -c:v libx264 -preset fast -crf 18 -pix_fmt yuv420p -c:a copy "${outputFile}"`;

  console.log(`  → ${cmd}\n`);
  execSync(cmd, { stdio: 'inherit' });
}

// ─── Main ────────────────────────────────────────────────────
async function main() {
  console.log('');
  console.log('╔══════════════════════════════════════╗');
  console.log('║   👻 GHOST CAPTIONS ENGINE V2        ║');
  console.log('║   Word-by-word viral highlights      ║');
  console.log('╚══════════════════════════════════════╝');
  console.log('');
  console.log(`  Style:     ${STYLE}`);
  console.log(`  Highlight: ${HIGHLIGHT_HEX} (${getArg('highlight', 'cyan')})`);
  console.log(`  Grouping:  ${GROUPING} words`);
  console.log(`  Position:  ${POSITION}`);
  console.log(`  Font:      ${FONT}`);
  console.log(`  Pop:       ${POP_SCALE}%`);
  console.log(`  Mode:      ${ASS_ONLY ? 'ASS subtitle only' : 'Full burn-in'}`);
  console.log('');

  // 1. Resolve audio source
  let audioSource = AUDIO;
  let tempAudio = null;

  if (!WORDS_FILE && !AUDIO) {
    // Auto-extract audio from video
    audioSource = extractAudioFromVideo(VIDEO);
    tempAudio = audioSource; // Track for cleanup
  }

  // 2. Get word timestamps
  const data = getWordTimestamps(audioSource);
  const words = data.words || [];
  console.log(`✅ Got ${words.length} word timestamps\n`);

  if (words.length === 0) {
    console.error('❌ No words detected in audio. Check the audio quality.');
    process.exit(1);
  }

  // 3. Group words
  const groups = groupWords(words, GROUPING);
  console.log(`✅ Created ${groups.length} word groups (${GROUPING} words each)\n`);

  // 4. Generate ASS
  const ass = generateASS(groups);

  if (ASS_ONLY) {
    const assOut = OUTPUT.endsWith('.ass') ? OUTPUT : OUTPUT.replace(/\.\w+$/, '.ass');
    fs.writeFileSync(assOut, ass);
    console.log(`✅ ASS subtitle file saved: ${assOut}\n`);
    console.log('   Import this file into Final Cut Pro, Premiere Pro, or DaVinci Resolve');
  } else {
    const assPath = path.join('/tmp', `ghost-captions-${Date.now()}.ass`);
    fs.writeFileSync(assPath, ass);
    console.log(`✅ Generated ASS subtitle file: ${assPath}\n`);

    // 5. Burn subtitles
    burnSubtitles(assPath, VIDEO, OUTPUT);

    // Cleanup temp ASS
    try { fs.unlinkSync(assPath); } catch {}
  }

  // Cleanup temp audio
  if (tempAudio) {
    try { fs.unlinkSync(tempAudio); } catch {}
  }

  console.log('');
  console.log(`🎬 Done! Output: ${OUTPUT}`);
  console.log('');
}

main().catch(err => {
  console.error('❌ Error:', err.message);
  process.exit(1);
});
