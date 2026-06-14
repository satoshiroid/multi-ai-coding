# design artifacts

## sketch_prompts

```json
[
  "Design a minimalist task management app UI with a light color scheme. Use a white background with blue accents for active tasks, green for completed tasks, and red for overdue warnings. Arrange a fixed header at the top, a task addition form below it, and a scrollable task list area. Each task card should have a title, details, due date, a checkbox for completion, and edit/delete buttons.",
  "Create a dark-themed task management app UI. Utilize a dark gray background with neon blue for active tasks, lime green for completed tasks, and bright red for overdue warnings. The layout should feature a collapsible side menu, a central task list area, and a floating action button for adding tasks. Task cards should include a title, details, due date, completion checkbox, and action icons for edit/delete.",
  "Design a vibrant task management app UI with a colorful palette. Use a gradient background transitioning from purple to pink, with white text for active tasks, light green for completed tasks, and orange for overdue warnings. Implement a top navigation bar, a task input field at the bottom, and a grid layout for task cards. Each card should display a title, details, due date, a toggle for completion, and buttons for edit/delete."
]
```

## design_spec

```json
{
  "screens": [
    "Home",
    "Task Details",
    "Edit Task"
  ],
  "components": {
    "header": {
      "position": "top",
      "elements": [
        "logo",
        "navigation"
      ]
    },
    "task_addition_form": {
      "position": "below_header",
      "elements": [
        "input_field",
        "add_button"
      ]
    },
    "task_list_area": {
      "position": "center",
      "elements": [
        "task_cards"
      ]
    },
    "task_card": {
      "elements": [
        "title",
        "details",
        "due_date",
        "completion_checkbox",
        "edit_button",
        "delete_button"
      ]
    },
    "modal_edit_form": {
      "elements": [
        "title_input",
        "details_input",
        "due_date_picker",
        "save_button",
        "cancel_button"
      ]
    }
  },
  "transitions": {
    "home_to_task_details": "tap_task_card",
    "task_details_to_edit_task": "tap_edit_button",
    "edit_task_to_home": "tap_save_button"
  },
  "colors": {
    "active_task": {
      "background": "#FFFFFF",
      "text": "#0000FF"
    },
    "completed_task": {
      "background": "#00FF00",
      "text": "#FFFFFF"
    },
    "overdue_task": {
      "background": "#FF0000",
      "text": "#FFFFFF"
    }
  },
  "typography": {
    "font_family": "Arial, sans-serif",
    "font_size": "14px",
    "font_weight": "normal"
  },
  "design_tokens": {
    "spacing": "8px",
    "border_radius": "4px",
    "box_shadow": "0 2px 4px rgba(0,0,0,0.1)"
  }
}
```
