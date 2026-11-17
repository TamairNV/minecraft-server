package uk.tamairnv.dimlock;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.format.NamedTextColor;
import org.bukkit.Bukkit;
import org.bukkit.World;
import org.bukkit.command.Command;
import org.bukkit.command.CommandSender;
import org.bukkit.entity.Entity;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.EventPriority;
import org.bukkit.event.Listener;
import org.bukkit.event.entity.EntityPortalEvent;
import org.bukkit.event.player.PlayerPortalEvent;
import org.bukkit.plugin.java.JavaPlugin;

/**
 * Locks portal travel into the Nether and the End until an admin unlocks it.
 * Anyone already inside a locked dimension can stay and leave; only travel in is blocked.
 * Commands that teleport (e.g. /tp, /execute in) are not blocked, so ops can still go in.
 * State lives in config.yml (locked.nether, locked.end); the admin site reads it and sends /dimlock.
 */
public final class DimLock extends JavaPlugin implements Listener {
    private static final Map<String, String> NAMES = Map.of("nether", "The Nether", "end", "The End");
    private final Map<UUID, Long> lastWarned = new HashMap<>();

    @Override
    public void onEnable() {
        saveDefaultConfig();
        getServer().getPluginManager().registerEvents(this, this);
        getLogger().info("Nether " + state("nether") + ", End " + state("end"));
    }

    private boolean locked(String dim) {
        return getConfig().getBoolean("locked." + dim, true);
    }

    private String state(String dim) {
        return locked(dim) ? "locked" : "unlocked";
    }

    private static String dimOf(World world) {
        if (world == null) return null;
        return switch (world.getEnvironment()) {
            case NETHER -> "nether";
            case THE_END -> "end";
            default -> null;
        };
    }

    /** The dimension this trip goes into, if it's a different (lockable) one. */
    private String lockedTarget(Entity entity, World to) {
        String dim = dimOf(to);
        if (dim == null || !locked(dim)) return null;
        if (dim.equals(dimOf(entity.getWorld()))) return null;
        return dim;
    }

    @EventHandler(priority = EventPriority.HIGHEST, ignoreCancelled = true)
    public void onPlayerPortal(PlayerPortalEvent event) {
        String dim = lockedTarget(event.getPlayer(), event.getTo().getWorld());
        if (dim == null) return;
        event.setCancelled(true);
        warn(event.getPlayer(), dim);
    }

    @EventHandler(priority = EventPriority.HIGHEST, ignoreCancelled = true)
    public void onEntityPortal(EntityPortalEvent event) {
        if (event.getTo() == null) return;
        if (lockedTarget(event.getEntity(), event.getTo().getWorld()) != null) event.setCancelled(true);
    }

    private void warn(Player player, String dim) {
        long now = System.currentTimeMillis();
        Long last = lastWarned.get(player.getUniqueId());
        if (last != null && now - last < 3000) return;
        lastWarned.put(player.getUniqueId(), now);
        player.sendActionBar(Component.text(NAMES.get(dim) + " is locked for now.", NamedTextColor.RED));
    }

    @Override
    public boolean onCommand(CommandSender sender, Command command, String label, String[] args) {
        if (args.length != 2 || !NAMES.containsKey(args[0])) return false;
        String dim = args[0];
        switch (args[1]) {
            case "status" -> {}
            case "lock", "unlock" -> {
                boolean lock = args[1].equals("lock");
                boolean changed = locked(dim) != lock;
                getConfig().set("locked." + dim, lock);
                saveConfig();
                if (changed && !lock) announce(NAMES.get(dim) + " is now open!");
            }
            default -> {
                return false;
            }
        }
        // The site parses this exact line from the log.
        sender.sendMessage("DimLock: " + dim + " is " + state(dim));
        return true;
    }

    private void announce(String text) {
        Bukkit.broadcast(Component.text(text, NamedTextColor.GOLD));
        if (getServer().getPluginManager().isPluginEnabled("DiscordSRV")) {
            Bukkit.dispatchCommand(Bukkit.getConsoleSender(), "discord broadcast " + text);
        }
    }

    @Override
    public List<String> onTabComplete(CommandSender sender, Command command, String alias, String[] args) {
        if (args.length == 1) return List.of("nether", "end");
        if (args.length == 2) return List.of("lock", "unlock", "status");
        return List.of();
    }
}
