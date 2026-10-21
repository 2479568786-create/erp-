# OZON Listing Assistant

This extension is for developer use and internal packaging only.

## Source files

- `manifest.json`
- `background.js`
- `content.js`
- `content.css`
- `extractors.js`
- `config.js`
- `popup.html`
- `popup.js`
- `icons/`

## Local testing

1. Open `edge://extensions`.
2. Turn on Developer mode.
3. Load the `extension/` folder as an unpacked extension only in a test profile.
4. Visit 1688, Taobao, or Tmall product pages and confirm the popup and page collector work.
5. Check the service worker and popup console if anything fails.

## Packaging rule

Employees should not use unpacked loading for normal installation. Use the packaged distribution or a managed rollout path instead.
