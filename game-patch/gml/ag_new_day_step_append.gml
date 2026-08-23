// The original new_day object is the authoritative end-of-shift transition.
// Advance the in-session Open Shift day here, independently of saving.
if (global.cur_day >= 1001 && global.ag_story_day_advance_applied == 0)
{
    global.ag_story_day += 1;
    global.datestring = "O.S. DAY " + string(global.ag_story_day);
    global.ag_story_day_advance_applied = 1;
    global.ag_prefetch_ready = 0;
    global.ag_prefetch_day = 0;
    global.ag_preload_notice_day = 0;
}
