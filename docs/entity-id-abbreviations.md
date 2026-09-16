# Entity ID abbreviations

Automatic IDs use the virtual entity name, not the Device name. For example,
`Master Bedroom Ceiling Lamp` becomes `light.room_mbed_clight` for a light.
The Home Assistant domain prefix is never abbreviated.

Matching ignores case and accepts spaces, underscores, and hyphens. Complete
words/phrases are matched, with the longest phrase taking precedence:
`air quality index` becomes `aqi`, while `air quality` becomes `aq`.
Abbreviations are applied before the 80-character object-ID limit.

Existing saved IDs and explicit custom IDs are preserved. Clear the ID field
to regenerate from the current name. Different aliases can produce the same
ID; source collisions get `_copy`, and other occupied IDs must be corrected in
the form (for example, by adding a room name or number).

## Full dictionary

156 supported English words and phrases.

| Name | Abbreviation |
| --- | --- |
| absolute humidity | `ahumi` |
| access point | `ap` |
| air conditioner | `airc` |
| air pressure | `apres` |
| air purifier | `apur` |
| air quality | `aq` |
| air quality index | `aqi` |
| atmospheric pressure | `apres` |
| attic | `area_att` |
| back door | `bdoor` |
| backyard | `area_by` |
| balcony | `area_bal` |
| basement | `area_bsmt` |
| bathroom | `room_bt` |
| battery | `batt` |
| battery level | `batt_lvl` |
| battery low | `batt_low` |
| bedroom | `room_bed` |
| brightness | `bri` |
| bulb | `bb` |
| camera | `cctv` |
| carbon dioxide | `co2` |
| carbon monoxide | `co` |
| cctv | `cctv` |
| ceiling fan | `cfan` |
| ceiling lamp | `clight` |
| ceiling light | `clight` |
| children room | `room_kid` |
| circulation pump | `cpump` |
| coffee machine | `coffee` |
| color temperature | `ctemp` |
| colour temperature | `ctemp` |
| curtain | `curt` |
| dehumidifier | `dhmdf` |
| desk lamp | `dlamp` |
| dew point | `dew` |
| dining room | `room_dn` |
| dishwasher | `dwash` |
| door lock | `dlock` |
| doorbell | `dbell` |
| doorstep | `area_d` |
| downlight | `dlight` |
| download speed | `dl_spd` |
| dressing room | `room_dr` |
| driveway | `area_dw` |
| elapsed time | `elapsed` |
| electric current | `amps` |
| energy consumption | `energy` |
| entrance | `room_e` |
| exhaust fan | `efan` |
| filter life | `flt_life` |
| filter remaining | `flt_remain` |
| floor heating | `fheat` |
| floor lamp | `flamp` |
| focused light | `fclight` |
| formaldehyde | `h2ho` |
| freezer | `frzr` |
| frequency | `freq` |
| fridge | `frdg` |
| front door | `fdoor` |
| front yard | `area_fy` |
| garage | `area_grg` |
| garage door | `gdoor` |
| garden | `area_gdn` |
| gas consumption | `guse` |
| guest bedroom | `room_gbed` |
| guest room | `room_gst` |
| hallway | `area_h` |
| heat pump | `hpump` |
| heating and air conditioning system | `hvac` |
| home office | `room_off` |
| humidifier | `hmdf` |
| humidity | `humi` |
| illuminance | `ill` |
| illumination | `ill` |
| indirect | `ind` |
| irrigation | `irrig` |
| kids room | `room_kid` |
| kitchen | `room_k` |
| laundry room | `room_ld` |
| led strip | `strip` |
| light bulb | `bb` |
| lighting controller | `light` |
| lightling controller | `light` |
| link quality | `lqi` |
| living room | `room_lv` |
| master bedroom | `room_mbed` |
| media player | `media` |
| microwave | `mw` |
| microwave oven | `mw` |
| network attached storage | `nas` |
| night light | `nlight` |
| occupancy | `occ` |
| particulate matter | `pm` |
| pendant light | `plight` |
| powder | `pd` |
| powder room | `room_pd` |
| power consumption | `pwr` |
| power factor | `pf` |
| power strip | `pstrip` |
| presence | `pres` |
| primary bedroom | `room_mbed` |
| radon | `radon` |
| rainfall | `rain` |
| range hood | `rhood` |
| refrigerator | `frdg` |
| relative humidity | `rhumi` |
| remaining time | `remain` |
| remote control | `remote` |
| robot vacuum | `rvcu` |
| robot vacuum cleaner | `rvcu` |
| roller blind | `rblind` |
| roller shutter | `rshutter` |
| server room | `room_s` |
| signal strength | `sig` |
| smart plug | `plug` |
| smoke | `smoke` |
| soil moisture | `soil_moist` |
| spotlight | `spot` |
| staircase | `area_stair` |
| storage room | `room_str` |
| strip light | `strip` |
| study room | `room_st` |
| table lamp | `tlamp` |
| television | `tv` |
| temperature | `temp` |
| terrace | `area_ter` |
| thermostat | `tstat` |
| total volatile organic compound | `tvoc` |
| total volatile organic compounds | `tvoc` |
| track light | `tlight` |
| tumble dryer | `tdry` |
| ultraviolet index | `uvi` |
| under cabinet light | `uclight` |
| uninterruptible power supply | `ups` |
| upload speed | `ul_spd` |
| utility room | `room_ut` |
| vacuum cleaner | `vcu` |
| ventilation | `vent` |
| vibration | `vib` |
| volatile organic compound | `voc` |
| volatile organic compounds | `voc` |
| voltage | `volt` |
| walk in closet | `room_wc` |
| wall lamp | `wlight` |
| wall light | `wlight` |
| washing machine | `wm` |
| water consumption | `wuse` |
| water flow | `wflow` |
| water heater | `whtr` |
| water leak | `wleak` |
| water pressure | `wpres` |
| water pump | `wpump` |
| water purifier | `wpur` |
| wind direction | `wdir` |
| wind speed | `wspd` |
