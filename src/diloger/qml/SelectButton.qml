import QtQuick
import QtQuick.Controls.Basic

// The one button every content card uses to choose its source.
//
// It is a whole component rather than a styled Button so the selected state can
// read as "Selected" instead of a greyed-out button nobody can interpret, and so
// the geometry is fixed here instead of drifting from card to card. A card places
// one of these and never wraps it in a MouseArea of its own: an overlapping area
// would swallow the click before the button ever sees it.
Button {
    id: control

    // What this card shows: the label to use, and whether it is the chosen one.
    property string selectedLabel: "Selected"
    property bool selected: false

    // Fixed geometry, so every card aligns without repeating numbers.
    implicitWidth: 116
    implicitHeight: 34

    text: selected ? selectedLabel : "Select"

    // Choosing the same file again is a no-op, so the button stays live in both
    // states rather than looking unavailable while it is already the answer.
    enabled: true

    background: Rectangle {
        radius: theme.radiusSmall
        color: {
            if (!control.enabled)
                return theme.sunk;
            if (control.selected)
                return theme.accentDim;
            if (control.down)
                return theme.sunk;
            if (control.hovered)
                return theme.hover;
            return theme.panelAlt;
        }
        border.width: (control.visualFocus || control.down) ? 2 : 1
        border.color: control.visualFocus ? theme.focus
                                         : (control.selected ? theme.accent
                                                             : theme.border)

        Behavior on color {
            ColorAnimation { duration: 90 }
        }
    }

    contentItem: Text {
        text: control.text
        color: !control.enabled ? theme.textFaint
                                : (control.selected ? theme.accent : theme.text)
        font.pixelSize: 14
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
}