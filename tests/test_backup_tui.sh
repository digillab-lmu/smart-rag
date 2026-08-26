#!/usr/bin/env bash
# Making, carrying and restoring a backup from the interface, not from memory.
#
# Asked for after the transfer to a second machine turned out to have no
# direct network path between the two: "wäre es nicht besser, das Backup über
# die TUI anfertigen zu können und dann über die TUI dieses auf einen
# Wechseldatenträger kopieren zu können?" — and then the other half, that
# bootstrap and the menu should be able to restore one, including from a
# removable medium.
#
# Three properties matter more than the menu entries themselves:
#
#   * **The checksum travels with the archive.** restore.sh verifies it when
#     it is there and only warns when it is not, so a copy made by hand — the
#     one most likely to be truncated in transit — is the one least likely to
#     be checked. The copy step takes both files and reads the result back
#     off the medium.
#   * **A restore is announced before it happens.** The dry run performs
#     every refusal without writing, and the confirmation has to be typed.
#   * **Restoring is offered where a move actually lands**: bootstrap, on a
#     machine with nothing on it, before it generates a set of secrets that
#     a restore would discard minutes later.
#
# Static checks. Driving the menu would need a terminal and Docker; what is
# checkable here is that the paths exist, are wired to the scripts, and carry
# the guards.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1
FAILURES=()
check() { if (( $2 != 0 )); then FAILURES+=("$1: $3"); fi; }

ADMIN="$(cat scripts/admin.sh)"
BOOT="$(cat scripts/bootstrap.sh)"
COMMON="$(cat scripts/lib/common.sh)"

# ─── The three entries exist and are wired ──────────────────────────────────
grep -q "action_backup_copy" <<<"$ADMIN"
check "the menu can copy a backup to a medium" $? ""
grep -q "action_restore" <<<"$ADMIN"
check "and can restore one" $? ""
grep -qE 'admin_backup_copy' <<<"$ADMIN"
check "the copy entry is offered in the backup menu" $? ""

# The restore entry used to print two command lines and return. That is not a
# restore, and on the machine a move lands on it is an instruction to leave
# the tool.
! grep -q "admin_backup_restore_manual" <<<"$ADMIN"
check "the restore entry no longer just prints commands" $? \
      "printing the command line is what sends somebody to type an archive name by hand"

# ─── The copy takes both files and checks the result ────────────────────────
copy_body="$(awk '/^action_backup_copy\(\) \{/,/^\}/' scripts/admin.sh)"
grep -q 'cp -- "\$archive.sha256"' <<<"$copy_body"
check "the checksum file is copied with the archive" $? \
      "without it a restore from the copy proceeds unchecked"
grep -q "^    sync$" <<<"$copy_body"
check "the copy is flushed before the medium is called safe" $? \
      "a stick pulled before this holds a file that looks complete"
grep -q "sha256sum \"\$target/" <<<"$copy_body"
check "the copy is read back off the medium and checked" $? \
      "this is what catches a full disk, a failing stick and a short write"
grep -q "admin_copy_secrets_warning" <<<"$copy_body"
check "the operator is told the archive holds every secret in clear" $? \
      "it contains .env; on a lost medium that is the whole installation"

# ─── The restore announces itself ───────────────────────────────────────────
restore_body="$(awk '/^action_restore\(\) \{/,/^\}/' scripts/admin.sh)"
grep -q -- "--dry-run" <<<"$restore_body"
check "the menu dry-runs before restoring" $? \
      "every refusal in restore.sh happens before anything is touched, so the "
grep -q "admin_restore_type_word" <<<"$restore_body"
check "and the confirmation has to be typed" $? \
      "an arrow key is not enough to replace an installation"
grep -q "admin_restore_rename_ask" <<<"$restore_body"
check "a different address is offered as a rename" $? \
      "a machine with another address needs a rename, not a copy"

# ─── Bootstrap offers it where a move lands ─────────────────────────────────
# The option itself, in the choice — not merely the word somewhere in the
# file, which every message beneath it also contains.
grep -qF '"$(t start_restore)"' <<<"$BOOT"
check "bootstrap offers restoring instead of a fresh install" $? \
      "the entry has to be one of the choices, not just a message that exists"
grep -qF '"$(t start_fresh)"' <<<"$BOOT"
check "and setting up a new one is still the other choice" $? ""
grep -q 'PREV_STATE" == "none"' <<<"$BOOT"
check "and asks only on a machine with nothing on it" $? \
      "an installation that already exists has its own menu for this"
boot_block="$(sed -n '/Fresh install, or take over an existing one/,/if \[\[ "\$PREV_STATE" != "none" \]\]/p' scripts/bootstrap.sh)"
grep -q -- "--dry-run" <<<"$boot_block"
check "bootstrap dry-runs too" $? ""
grep -q "start_restore_fallthrough" <<<"$boot_block"
check "a refused archive leaves a way forward" $? \
      "an installer that exits at that point leaves the machine unusable"

# ─── The chooser looks where an archive can be ──────────────────────────────
chooser="$(awk '/^choose_backup_archive\(\) \{/,/^\}/' scripts/lib/common.sh)"
# Mounted *and* unmounted. On the machine a restore lands on there is no
# backup directory and nothing has mounted the stick, so a chooser that looks
# only at mounted media offers nothing but a path to type — which is what it
# did on the first real restore.
grep -q "removable_partitions" <<<"$chooser"
check "the chooser sees media whether or not they are mounted" $? \
      "on the machine being restored onto, nothing has mounted the stick"
grep -q "mount_removable" <<<"$chooser"
check "and mounts one that is chosen to be searched" $? ""
grep -q "archive_pick_none_on_medium" <<<"$chooser"
check "a medium with no archive on it says so" $? \
      "rather than returning an empty selection"
grep -q "archive_pick_other" <<<"$chooser"
check "and a path that can be typed" $? ""
grep -q 'RM,SIZE,MOUNTPOINT' <<<"$COMMON"
check "removable media are taken from the kernel, not guessed" $? \
      "a wrong guess would read from, or write to, the wrong disk"
grep -q '\$4 != "/"' <<<"$COMMON"
check "and the root filesystem is never offered as one" $? ""

# ─── The target is chosen, not typed ────────────────────────────────────────
# On a server nothing mounts a stick automatically, so the first version found
# no removable media and left the operator typing a path — the interface
# giving up exactly where it should help.
grep -q "removable_partitions" <<<"$copy_body"
check "the copy lists media whether or not they are mounted" $? \
      "a server has no desktop to mount one, so an unmounted medium is the "
grep -q "mount_removable" <<<"$copy_body"
check "and mounts the one that is chosen" $? ""
# The condition, not the variable: a mutation that unmounts unconditionally
# leaves the name in the body and passed a check that only looked for it.
grep -qF 'if [[ -n "${MOUNTED_HERE:-}" ]]; then' <<<"$copy_body"
check "unmounting afterwards applies only to what it mounted itself" $? \
      "a medium the operator had already mounted may be in use for something else"
grep -q "admin_copy_other" <<<"$copy_body"
check "a path can still be typed" $? "for a network share or anything unrecognised"

partitions="$(awk '/^removable_partitions\(\) \{/,/^\}/' scripts/lib/common.sh)"
grep -q "RM" <<<"$partitions"
check "removable is taken from the kernel flag" $? ""

# ─── Formatting: the guards are the feature ─────────────────────────────────
fmt="$(awk '/^action_format_medium\(\) \{/,/^\}/' scripts/admin.sh)"
grep -q "removable_disks" <<<"$fmt"
check "only removable disks can be formatted" $? \
      "the system disk is not removable and therefore cannot be listed"
disks="$(awk '/^removable_disks\(\) \{/,/^\}/' scripts/lib/common.sh)"
grep -q '\$2 == 1' <<<"$disks"
check "and that is decided by the kernel flag, not by a name" $? \
      "a name filter is defeated by a different disk layout"
# Again the condition rather than the message: disabling the test leaves the
# error string in place, and the first version of this check passed on it.
grep -qF 'lsblk -nr -o MOUNTPOINT "$device" 2>/dev/null | grep -q .' <<<"$fmt"
check "a device with something mounted from it is refused" $? \
      "unmounting on the operator's behalf decides that whatever uses the disk does not matter"
grep -q "admin_format_mounted" <<<"$fmt"
check "and told so" $? ""
grep -q "admin_format_type_device" <<<"$fmt"
check "the device node has to be typed out" $? \
      "selecting is what makes taking the wrong row easy"
grep -q 'lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINT' <<<"$fmt"
check "what is on the disk is shown before it is erased" $? \
      "that is how somebody recognises the wrong disk in time"
grep -q "mkfs.ext4" <<<"$fmt"
check "the medium is formatted ext4" $? \
      "FAT32 cannot hold a file over 4 GB, which a real backup passes"
grep -q "admin_backup_format" <<<"$ADMIN"
check "and the entry is in the backup menu" $? ""

if (( ${#FAILURES[@]} > 0 )); then
    echo "FAILURES:"; printf '  - %s\n' "${FAILURES[@]}"; exit 1
fi
echo "All backup-TUI checks passed: the menu can make a backup, copy it to a"
echo "removable medium and restore one; the copy takes the checksum file with"
echo "it, flushes to the medium and reads the result back before calling it"
echo "done, and says that the archive holds every password in clear; the"
echo "restore dry-runs first, needs a typed confirmation and offers the rename"
echo "a different address requires; bootstrap offers the same restore on a"
echo "machine with nothing on it and still has a way forward if the archive is"
echo "refused; and removable media come from the kernel rather than a guess,"
echo "with the root filesystem never among them."
