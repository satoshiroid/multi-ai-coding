# design artifacts

## sketch_prompts

```json
[
  "Design a task management app UI with a clean, minimalistic layout featuring a blue primary color scheme. Include a task card layout with title, details, due date, completion checkbox, and edit/delete buttons. Use a tabbed navigation for filtering tasks by status (all, incomplete, completed, overdue).",
  "Create a task management app UI with a modern, bold aesthetic using a dark mode theme. Emphasize task cards with contrasting colors for overdue tasks (red background) and completed tasks (strikethrough text). Implement a sidebar for task filtering and a floating action button for adding tasks.",
  "Visualize a task management app UI with a vibrant, playful design. Use a grid layout for task cards with interactive animations on hover. Include a top navigation bar for filtering tasks and a modal popup for task addition/editing."
]
```

## design_spec

```json
{
  "screens": [
    "Dashboard",
    "Task Details",
    "Task Edit/Add"
  ],
  "components": {
    "task_card": {
      "elements": [
        "title",
        "details",
        "due_date",
        "completion_checkbox",
        "edit_button",
        "delete_button"
      ],
      "styles": {
        "overdue": {
          "background": "var(--color-warning-bg)",
          "border": "1px solid var(--color-warning)",
          "text_color": "var(--color-warning-text)"
        },
        "completed": {
          "text_decoration": "line-through",
          "opacity": "0.6"
        }
      }
    },
    "task_form": {
      "type": "modal",
      "fields": [
        "title",
        "details",
        "due_date",
        "completion_checkbox"
      ]
    },
    "filter_ui": {
      "type": "tab_group",
      "options": [
        "all",
        "incomplete",
        "completed",
        "overdue"
      ]
    }
  },
  "transitions": {
    "hover": {
      "task_card": "transform: scale(1.02);"
    },
    "focus": {
      "input_fields": "border-color: var(--color-primary);"
    }
  },
  "responsive_breakpoints": {
    "mobile": "375px",
    "tablet": "768px",
    "desktop": "1280px"
  },
  "design_tokens": {
    "colors": {
      "primary": "#1E3A8A",
      "success": "#10B981",
      "warning": "#EF4444",
      "neutral": "#9CA3AF",
      "warning-bg": "#FEE2E2",
      "warning-text": "#B91C1C"
    },
    "typography": {
      "font_family": "'Inter', sans-serif",
      "base_font_size": "16px"
    },
    "spacing": {
      "base": "8px",
      "large": "16px"
    }
  }
}
```
