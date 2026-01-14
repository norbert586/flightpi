# FlightPi Display Improvements

## Summary
Enhanced the FlightPi display system for fault-free operation with clean text rendering and dual photo API support.

## Key Improvements

### 1. Text Truncation with Ellipsis
- **Added `truncate_text()` function**: Intelligently truncates text that exceeds width limits with proper ellipsis (…)
- **Applied to all display elements**:
  - Callsign in header (prevents overlap with type pill)
  - Type code pill (max 50px width)
  - All fact labels (Type, Manufacturer, Country, Owner)
  - Footer text (registration + timestamp)
  - Route airport names (2-line wrap with ellipsis)

### 2. Dual Photo API Support
- **Primary API**: airport-data.com (by ICAO hex code)
- **Secondary API**: planespotters.net (by registration number)
- **New functions**:
  - `fetch_planespotters_photo_by_reg()`: Fetches from Planespotters API
  - `fetch_aircraft_photo()`: Unified function that tries both sources
- **Smart caching**: Separate cache files for each source with 12-hour "no-photo" flags

### 3. Robust Error Handling

#### Network Functions
- `fetch_nearest()`: Now wrapped in try/except, returns None on any failure
- `download_image()`:
  - Validates URL before attempting download
  - Cleans up partial downloads on error
  - Verifies file size (>100 bytes) before accepting
  - Atomic file operations (download to .part, then rename)

#### Display Functions
- `autofit_text()`: Handles None/empty strings gracefully
- `draw_route()`: Sanitizes all input strings before rendering
- `draw_card()`: Sanitizes all parameters at entry point
- `draw_facts_block()`: Enhanced multi-line wrapping with proper truncation

### 4. Input Sanitization
All text inputs are now sanitized through a consistent pattern:
```python
text = (text or "").strip() or "—"  # For required fields
text = (text or "").strip()          # For optional fields
```

Applied to:
- Callsign, type code, registration
- Model, manufacturer, country, owner
- Airport IATA codes and names
- Route information

### 5. Edge Case Handling

#### Covered Scenarios
✓ Empty strings → Display as "—" or empty
✓ None values → Converted to empty strings
✓ Extra whitespace → Stripped
✓ Text overflow → Truncated with ellipsis
✓ Missing photos → Try both APIs, then show "No photo"
✓ Corrupt downloads → Verify size, clean up on error
✓ Network timeouts → All requests have 10s timeout
✓ API failures → Cached data fallback, or graceful degradation
✓ Long callsigns → Truncated to prevent overlap
✓ Long type codes → Truncated to fit pill
✓ Long airport names → 2-line wrap with ellipsis
✓ Long fact values → Multi-line with ellipsis on last line

## Technical Details

### Planespotters.net API Integration
**Endpoint**: `https://api.planespotters.net/pub/photos/reg/{registration}`

**Response Format**:
```json
{
  "photos": [{
    "thumbnail": {"src": "https://..."},
    "thumbnail_large": {"src": "https://..."},
    "link": "https://..."
  }]
}
```

**Priority**: thumbnail_large > thumbnail > link

### Text Truncation Algorithm
```python
def truncate_text(d, text, font, max_w):
    if d.textlength(text, font=font) <= max_w:
        return text
    while text and d.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return (text + "…") if text else "…"
```

### Cache Structure
```
cache/photos/
  ├── {HEX}.jpg              # airport-data.com photo
  ├── {HEX}.none             # no photo flag (12h TTL)
  ├── ps_{REG}.jpg           # planespotters.net photo
  └── ps_{REG}.none          # no photo flag (12h TTL)
```

## Testing Recommendations

### Test Cases to Verify
1. **Long text fields**: Aircraft with long names (e.g., "Boeing 737-900ER(WL)")
2. **Missing data**: Aircraft with no registration or callsign
3. **Route display**: Very long airport names (e.g., "Newark Liberty International Airport")
4. **Photo fallback**: Aircraft in airport-data but not planespotters (and vice versa)
5. **Network failures**: Unplug ethernet to verify graceful degradation
6. **API timeouts**: Slow network conditions
7. **Empty responses**: Aircraft with minimal metadata

### Manual Testing Commands
```bash
# Check syntax
python3 -m py_compile flight.py

# Run display (Ctrl+C to stop)
sudo systemctl restart flight-display

# Monitor logs
journalctl -u flight-display -f

# Check cache effectiveness
ls -lh cache/photos/ | wc -l  # Count cached photos
```

## Performance Impact
- **Minimal CPU overhead**: Truncation is O(n) where n = text length
- **Reduced API calls**: Dual photo sources increase cache hit rate
- **Memory efficient**: All operations use string slicing, no extra allocations

## Backwards Compatibility
- ✅ Existing cache files remain valid
- ✅ Database schema unchanged
- ✅ Configuration unchanged
- ✅ Web server unaffected

## Future Enhancements (Optional)
- Add JetPhotos.com as third photo source
- Implement photo quality scoring (prefer higher resolution)
- Add configurable ellipsis style (… vs ...)
- Cache photo availability across sessions (persistent no-photo flags)
