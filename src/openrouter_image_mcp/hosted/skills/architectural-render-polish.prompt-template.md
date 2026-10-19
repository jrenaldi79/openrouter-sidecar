# Render polish prompt template

Replace every `{placeholder}` with specifics from your inventory of the render, then delete any line that doesn't apply. Keep the two headings exactly as written: they are what holds the model to the original design.

```text
Edit this architectural {space_type} rendering into a photograph indistinguishable from a professional
{publication_type} shoot (shot on a full-frame camera, {lens} lens, natural exposure, true verticals).

KEEP EXACTLY THE SAME - do not move, add, remove or redesign anything:
- camera position, framing, perspective and {aspect_description} aspect ratio
- all architecture: {architecture}
- all furniture and objects and their positions: {furniture}
- the {floor_or_wall_pattern} and its direction
- the {people_count} people: same positions, poses, clothing colors and skin tones ({people})
- all signage and text exactly as shown: {signage}
- the {palette} palette and overall lighting direction ({light_direction})

IMPROVE ONLY PHOTOGRAPHIC REALISM:
- people: real human skin texture, natural faces, real hair, fabric folds and drape, natural relaxed posture
- materials: {materials}
- light: physically accurate {light_quality}, gentle bounce light, soft contact shadows, subtle reflections on {reflective_surfaces}, realistic glow from {light_fixtures} without blooming
- plants: {plants}
- camera realism: fine natural film grain, slight lens vignetting, accurate white balance

Do not add any new text overlays, logos, watermarks or brand names{extra_exclusions}.
```

## Filling it in

| Placeholder | What to write |
|---|---|
| `{space_type}` | interior, exterior, lobby, streetscape, aerial |
| `{publication_type}` | interiors magazine, architecture magazine, real-estate |
| `{lens}` | 24mm tilt-shift for interiors; 35mm or 50mm for closer views |
| `{aspect_description}` | wide, panoramic, square, portrait |
| `{architecture}` | Each element with its material and colour: columns, ceiling, fixtures, glazing and frames, windows. |
| `{furniture}` | Each piece with its material and where it sits. |
| `{floor_or_wall_pattern}` | e.g. herringbone oak floor, stacked-bond tile wall |
| `{people_count}`, `{people}` | One short clause per person: role or position, clothing colour, action. |
| `{signage}` | Every sign or graphic, or delete the line if there are none (and mask any that matter). |
| `{palette}` | warm earth-tone, cool neutral, high-contrast monochrome |
| `{light_direction}` | e.g. daylight from the left-hand windows, late afternoon |
| `{materials}` | Real-material cues for what's in the scene: wood grain and pores, honed stone with veining, matte plaster, brushed metal, real paper on books. |
| `{light_quality}` | soft daylight, overcast daylight, warm evening interior light |
| `{reflective_surfaces}` | e.g. the satin floor, the glass partitions |
| `{light_fixtures}` | e.g. the linear pendants and recessed downlights |
| `{plants}` | real leaves with translucency (delete if there are none) |
| `{extra_exclusions}` | e.g. ", no readable titles on book covers"; leave empty if none |

## Tightening for a rerun

When the drift check finds a change, add a line under KEEP EXACTLY that names the item and its place, for example:

```text
- the {object} stays exactly at {location}, same size, shape and color
- exactly {people_count} people, no additional figures
```

If a second rerun still drifts, protect the area with a black region in a mask instead of adding more words.
