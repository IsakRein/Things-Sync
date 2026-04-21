"""AppleScript snippets for Things 3 operations.

All scripts share a common prelude that defines field/record separators,
date conversion, and per-class serializers. Operations are concatenated
onto the prelude and executed via `_osascript.run()`.
"""
from __future__ import annotations

PRELUDE = r"""
property US : ASCII character 31
property RS : ASCII character 30

on pad2(n)
    set n to n as integer
    if n < 10 then return "0" & n
    return n as text
end pad2

on isoDate(d)
    if d is missing value then return ""
    try
        set y to year of d
        set m to (month of d as integer)
        set dd to day of d
        set hh to hours of d
        set mm to minutes of d
        set ss to seconds of d
        return (y as text) & "-" & my pad2(m) & "-" & my pad2(dd) & "T" & my pad2(hh) & ":" & my pad2(mm) & ":" & my pad2(ss)
    on error
        return ""
    end try
end isoDate

on parseISO(s)
    if s is "" then return missing value
    set d to current date
    set day of d to 1
    set year of d to (text 1 thru 4 of s) as integer
    set month of d to (text 6 thru 7 of s) as integer
    set day of d to (text 9 thru 10 of s) as integer
    if (length of s) > 10 then
        set hours of d to (text 12 thru 13 of s) as integer
        set minutes of d to (text 15 thru 16 of s) as integer
        set seconds of d to (text 18 thru 19 of s) as integer
    else
        set hours of d to 0
        set minutes of d to 0
        set seconds of d to 0
    end if
    return d
end parseISO

"""


# Each serializer returns a delimited record string (no trailing RS).
SERIALIZERS = r"""
on statusText(t)
    tell application id "com.culturedcode.ThingsMac"
        set s to status of t
        if s is open then return "open"
        if s is completed then return "completed"
        if s is canceled then return "canceled"
    end tell
    return "open"
end statusText

on serializeTodo(t)
    tell application id "com.culturedcode.ThingsMac"
        set theId to id of t
        set theName to name of t
        try
            set theNotes to notes of t
        on error
            set theNotes to ""
        end try
        set theTags to ""
        try
            set theTags to tag names of t
        end try
        set dDue to my isoDate(due date of t)
        set dAct to my isoDate(activation date of t)
        set dCmp to my isoDate(completion date of t)
        set dCnc to my isoDate(cancellation date of t)
        set dCre to my isoDate(creation date of t)
        set dMod to my isoDate(modification date of t)
        set theProj to ""
        try
            set theProj to id of (project of t)
        end try
        set theArea to ""
        try
            set theArea to id of (area of t)
        end try
        set theContact to ""
        try
            set theContact to id of (contact of t)
        end try
    end tell
    set theStatus to my statusText(t)
    return theId & US & theName & US & theNotes & US & theStatus & US & dDue & US & dAct & US & dCmp & US & dCnc & US & dCre & US & dMod & US & theTags & US & theProj & US & theArea & US & theContact
end serializeTodo

on serializeProject(p)
    tell application id "com.culturedcode.ThingsMac"
        set theId to id of p
        set theName to name of p
        try
            set theNotes to notes of p
        on error
            set theNotes to ""
        end try
        set theTags to ""
        try
            set theTags to tag names of p
        end try
        set dDue to my isoDate(due date of p)
        set dAct to my isoDate(activation date of p)
        set dCmp to my isoDate(completion date of p)
        set dCnc to my isoDate(cancellation date of p)
        set dCre to my isoDate(creation date of p)
        set dMod to my isoDate(modification date of p)
        set theArea to ""
        try
            set theArea to id of (area of p)
        end try
    end tell
    set theStatus to my statusText(p)
    return theId & US & theName & US & theNotes & US & theStatus & US & dDue & US & dAct & US & dCmp & US & dCnc & US & dCre & US & dMod & US & theTags & US & theArea
end serializeProject

on serializeArea(a)
    tell application id "com.culturedcode.ThingsMac"
        set theId to id of a
        set theName to name of a
        set theTags to ""
        try
            set theTags to tag names of a
        end try
        set theCol to "false"
        try
            if (collapsed of a) then set theCol to "true"
        end try
    end tell
    return theId & US & theName & US & theTags & US & theCol
end serializeArea

on serializeTag(g)
    tell application id "com.culturedcode.ThingsMac"
        set theId to id of g
        set theName to name of g
        set theShortcut to ""
        try
            set theShortcut to keyboard shortcut of g
            if theShortcut is missing value then set theShortcut to ""
        end try
        set theParent to ""
        try
            set p to parent tag of g
            if p is not missing value then set theParent to id of p
        end try
    end tell
    return theId & US & theName & US & theParent & US & theShortcut
end serializeTag

on serializeContact(c)
    tell application id "com.culturedcode.ThingsMac"
        set theId to id of c
        set theName to name of c
    end tell
    return theId & US & theName
end serializeContact

on serializeList(l)
    tell application id "com.culturedcode.ThingsMac"
        set theId to id of l
        set theName to name of l
    end tell
    return theId & US & theName
end serializeList
"""


def script(body: str) -> str:
    """Wrap an operation body with prelude + serializers."""
    return PRELUDE + "\n" + SERIALIZERS + "\n" + body
