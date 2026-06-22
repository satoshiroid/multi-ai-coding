# design artifacts

## sketch_prompts

```json
[
  "Design a clean, minimalist UI with a white and light gray color palette. The top section features a drag-and-drop area for uploading PPTX files, with a prominent upload button. Below, a two-column layout displays slides side by side with a thin separator and page number labels. Include a toggle switch in the header for difference highlighting.",
  "Create a vibrant, modern UI using a blue and green color scheme. The top area includes a bold, rounded upload button and drag-and-drop zone. The main section has a two-column layout with slides aligned horizontally, separated by a thick line and page numbers. A toggle button for highlighting differences is placed in the header.",
  "Design a professional, dark-themed UI with a black and dark blue color palette. The top section features a sleek drag-and-drop area and a subtle upload button. The slide comparison area uses a two-column layout with thin dividers and page numbers, and a toggle switch for highlighting differences is integrated into the header."
]
```

## design_spec

```json
{
  "screens": [
    "Upload Screen",
    "Comparison Screen"
  ],
  "components": {
    "Upload Area": {
      "position": "top",
      "features": [
        "drag-and-drop",
        "upload button"
      ]
    },
    "Comparison Layout": {
      "type": "two-column",
      "features": [
        "scrollable slides",
        "page number labels",
        "difference toggle"
      ]
    }
  },
  "transitions": {
    "Upload to Comparison": "on file upload"
  },
  "responsive_design": {
    "desktop": "50% columns",
    "mobile": "tab switch"
  },
  "color_palette": [
    "primary: #007BFF",
    "secondary: #6C757D",
    "background: #F8F9FA"
  ],
  "typography": {
    "font_family": "Arial, sans-serif",
    "font_sizes": {
      "header": "24px",
      "body": "16px"
    }
  }
}
```
