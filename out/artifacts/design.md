# design artifacts

## sketch_prompts

```json
[
  "Design a sleek, modern UI with a dark theme. Use a high-contrast color scheme with neon accents for interactive elements. The header should feature a minimalistic app title centered, with file upload buttons on either side. The main area is divided into two equal columns with slide viewers, and the footer contains navigation controls with a futuristic look.",
  "Create a clean, professional UI with a light theme. Utilize a blue and white color palette for a business-friendly appearance. The header includes the app title on the left and file upload buttons on the right. The main area is split into two columns for slide viewing, and the footer has simple, intuitive navigation controls with clear labels.",
  "Design a vibrant, user-friendly UI with a playful tone. Use a pastel color scheme with rounded edges for all components. The header features a large, bold app title with upload buttons below it. The main area is two-column with slide viewers, and the footer has oversized, colorful navigation buttons for easy interaction."
]
```

## design_spec

```json
{
  "screens": [
    "Main Screen"
  ],
  "components": {
    "header": [
      "App Title",
      "File Upload Buttons"
    ],
    "main_area": [
      "Left Slide Viewer",
      "Right Slide Viewer"
    ],
    "footer": [
      "Page Navigation Buttons",
      "Page Number Display",
      "Zoom Controls",
      "Difference Highlight Toggle"
    ]
  },
  "transitions": {
    "file_upload": "Auto-display page 1",
    "page_sync": "Sync page numbers",
    "zoom": "Simultaneous zoom",
    "highlight_toggle": "Pixel difference overlay"
  },
  "color_scheme": "High contrast, business-friendly",
  "typography": "Sans-serif, clear readability"
}
```
